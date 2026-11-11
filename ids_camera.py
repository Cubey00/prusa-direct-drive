import numpy as np

from ids_peak import ids_peak as peak
from ids_peak import ids_peak_ipl_extension

from ids_peak_ipl import ids_peak_ipl


class IDS_Camera:
    """
    just do:

        camera = IDS_Camera()
        image = camera.snap()
    """

    def __init__(self, camera_index=0):
        """
        Open the IDS camera and prepare it for image acquisition.

        camera_index=0 means:

            "Use the first IDS camera that is found."

        If you have multiple IDS cameras connected, you could use:

            IDS_Camera(camera_index=1)

        to use the second camera.
        """

        # These variables will hold the IDS objects we create below.
        self.device = None
        self.data_stream = None
        self.remote_nodemap = None

        # We use this to prevent accidentally using the camera after
        # camera.close() has been called.
        self._closed = False


        # ===================================================================
        # 1. START IDS PEAK
        # ===================================================================
        peak.Library.Initialize()


        try:

            # =================================================================
            # 2. FIND CONNECTED CAMERAS
            # =================================================================
            #
            # DeviceManager is IDS peak's way of finding cameras connected
            # to the computer.
            #
            # This can find cameras connected via USB3 Vision, such as
            # your U3-38L1XLS-C.
            #
            device_manager = peak.DeviceManager.Instance()

            # Ask the DeviceManager to rescan the computer for cameras.
            device_manager.Update()

            # Get the list of cameras that were found.
            devices = device_manager.Devices()


            # If the list is empty, there is no camera available.
            if devices.empty():
                raise RuntimeError(
                    "No IDS camera found. "
                    "Check that the camera is connected."
                )


            # Make sure the requested camera actually exists.
            if camera_index >= devices.size():
                raise RuntimeError(
                    f"Camera {camera_index} does not exist. "
                    f"Found {devices.size()} camera(s)."
                )


            # =================================================================
            # 3. OPEN THE CAMERA
            # =================================================================

            device_info = devices[camera_index]


            # Check whether another program is already using the camera.
            if not device_info.IsOpenable():
                raise RuntimeError(
                    "The IDS camera was found, but it cannot be opened. "
                    "Is IDS peak Cockpit or another program using it?"
                )


            # Actually open the camera.
            #
            # "Control" means that we want to be able to change camera
            # settings as well as acquire images.
            #
            self.device = device_info.OpenDevice(
                peak.DeviceAccessType_Control
            )


            # =================================================================
            # 4. GET THE CAMERA'S SETTINGS
            # =================================================================
            #
            # IDS cameras expose their settings through a GenICam "node map".
            #
            # You can think of this as a dictionary of camera controls:
            #
            #     ExposureTime
            #     Gain
            #     Width
            #     Height
            #     PixelFormat
            #     AcquisitionMode
            #     ...
            #
            # We save the node map because we will use it later.
            #
            self.remote_nodemap = (
                self.device.RemoteDevice().NodeMaps()[0]
            )


            # =================================================================
            # 5. OPEN THE CAMERA'S DATA STREAM
            # =================================================================

            self.data_stream = (
                self.device.DataStreams()[0].OpenDataStream()
            )


            # =================================================================
            # 6. WORK OUT HOW MUCH MEMORY EACH IMAGE NEEDS
            # =================================================================
            #
            # "PayloadSize" tells us how many bytes are required for one
            # image buffer.
            #
            # We need to give IDS enough memory to hold incoming images.
            #
            payload_size = self.remote_nodemap.FindNode(
                "PayloadSize"
            ).Value()


            # IDS tells us the minimum number of buffers required.
            #
            # We allocate that many buffers.
            #
            num_buffers = (
                self.data_stream.NumBuffersAnnouncedMinRequired()
            )


            # =================================================================
            # 7. CREATE AND QUEUE THE IMAGE BUFFERS
            # =================================================================
            #
            # This is one of the more confusing parts of IDS peak.
            #
            # The camera needs somewhere to put each image it captures.
            #
            # Once queued, the camera can fill it with an image.
            #
            for _ in range(num_buffers):

                buffer = self.data_stream.AllocAndAnnounceBuffer(
                    payload_size
                )

                # Tell the camera that this buffer is available.
                self.data_stream.QueueBuffer(buffer)


            # =================================================================
            # 8. LOCK CAMERA PARAMETERS
            # =================================================================
            #
            # TLParamsLocked prevents certain transport-layer parameters
            # from being changed while acquisition is running.
            #
            # This is mainly housekeeping required by the GenICam interface.
            #
            self.remote_nodemap.FindNode(
                "TLParamsLocked"
            ).SetValue(1)


            # =================================================================
            # 9. START THE DATA STREAM
            # =================================================================
            #
            # Start receiving image data from the camera.
            #
            self.data_stream.StartAcquisition()


            # =================================================================
            # 10. TELL THE CAMERA TO START ACQUIRING
            # =================================================================
            #
            # The previous command started the computer-side data stream.
            #
            # This command tells the actual camera to start its acquisition.
            #
            self.remote_nodemap.FindNode(
                "AcquisitionStart"
            ).Execute()


        except Exception:

            # If anything went wrong while setting up the camera,
            # make sure we clean up whatever was successfully created.
            self.close()

            # Then pass the original error back to the user.
            raise


    # =========================================================================
    # SNAP AND FLUSH
    # =========================================================================

    def flush(self):
        """
        Discard any stale/queued frames so the next real capture is fresh.
        Unlike data_stream.Flush(DiscardAll), this works while acquisition
        is running, by draining ready buffers with a short timeout instead.
        """
        if self._closed:
            raise RuntimeError("Camera has been closed.")

        while True:
            try:
                # Very short timeout: if a frame is already sitting ready,
                # this returns almost instantly. If none is ready, it times
                # out quickly and we know the queue is drained.
                buffer = self.data_stream.WaitForFinishedBuffer(10)  # 10 ms
            except Exception:
                # Timeout (no more buffered frames) -> queue is empty, done.
                break

            # Put the stale buffer straight back for reuse, without processing it.
            self.data_stream.QueueBuffer(buffer)

    def snap(self):
        """
        Capture one image.

        Returns
        -------
        numpy.ndarray

            For your colour camera this should be approximately:

                (3040, 4056, 3)

            corresponding to:

                height × width × RGB channels

        The returned NumPy array is independent of the IDS buffer,
        so you can safely keep it after this function returns.
        """

        # Don't allow somebody to take a picture after camera.close().
        if self._closed:
            raise RuntimeError(
                "Camera has been closed."
            )


        # =====================================================================
        # 1. WAIT FOR AN IMAGE
        # =====================================================================
        #
        # The camera is continuously acquiring.
        #
        # WaitForFinishedBuffer() waits until the camera has filled one
        # of our buffers with an image.
        #
        # 5000 means:
        #
        #     wait a maximum of 5000 milliseconds = 5 seconds
        #
        # If no image arrives within 5 seconds, IDS will raise an error.
        #
        buffer = self.data_stream.WaitForFinishedBuffer(5000)


        try:

            # =================================================================
            # 2. TURN THE IDS BUFFER INTO AN IDS IMAGE
            # =================================================================
            #
            # At this point "buffer" is essentially a chunk of memory
            # containing the raw image received from the camera.
            #
            # BufferToImage() makes it available as an IDS IPL image,
            # which gives us convenient image-processing functions.
            #
            image = ids_peak_ipl_extension.BufferToImage(
                buffer
            )


            # =================================================================
            # 3. CONVERT THE CAMERA'S COLOUR DATA
            # =================================================================
            #
            # Your U3-38L1XLS-C is a colour camera.
            #
            # Its sensor uses a Bayer colour filter, so the raw camera
            # image isn't necessarily:
            #
            #     [R G B]
            #     [R G B]
            #
            # for every pixel.
            #
            # Instead, the pixels contain a Bayer pattern from which the
            # RGB image needs to be reconstructed ("debayered").
            #
            # IDS IPL can perform this conversion for us.
            #
            # We convert to RGB8:
            #
            #     RGB
            #     8 bits per colour channel
            #
            # giving us the familiar NumPy representation:
            #
            #     height × width × 3
            #
            image = image.ConvertTo(
                ids_peak_ipl.PixelFormatName_RGB8
            )


            # =================================================================
            # 4. GET THE IMAGE AS A NUMPY ARRAY
            # =================================================================
            #
            # IDS IPL provides the image data in a NumPy-compatible form.
            #
            # get_numpy_3D() is appropriate for our RGB image because
            # it has three dimensions:
            #
            #     height
            #     width
            #     colour channel
            #
            # So we should get:
            #
            #     (3040, 4056, 3)
            #
            # for a full-resolution image from your camera.
            #
            array = image.get_numpy_3D()


            # =================================================================
            # 5. MAKE OUR OWN COPY
            # =================================================================
            #
            # This is VERY important.
            #
            # "array" is associated with the IDS image/buffer.
            #
            # We are about to return the camera buffer to IDS so that
            # it can be reused for the next image.
            #
            # Therefore we make a completely independent NumPy copy.
            #
            return np.array(array, copy=True)


        finally:

            # =================================================================
            # 6. RETURN THE BUFFER TO IDS
            # =================================================================
            #
            # We have finished using this buffer.
            #
            # Put it back into the queue so that the camera can use it
            # again for another image.
            #
            self.data_stream.QueueBuffer(buffer)


    # =========================================================================
    # EXPOSURE
    # =========================================================================

    def set_exposure(self, exposure_us):
        """
        Set the camera exposure time.

        Parameters
        ----------
        exposure_us : float
            Exposure time in microseconds.

        Examples
        --------
        1000  = 1 ms
        5000  = 5 ms
        10000 = 10 ms
        """

        # Find the camera's ExposureTime setting.
        exposure_node = self.remote_nodemap.FindNode(
            "ExposureTime"
        )

        # Set it.
        exposure_node.SetValue(exposure_us)


    # =========================================================================
    # GAIN
    # =========================================================================

    def set_gain(self, gain):
        """
        Set the camera gain.

        The exact valid range depends on the camera configuration.
        """

        # Find the camera's Gain setting.
        gain_node = self.remote_nodemap.FindNode(
            "Gain"
        )

        # Set the requested gain.
        gain_node.SetValue(gain)


    # =========================================================================
    # CLOSE
    # =========================================================================

    def close(self):
        """
        Properly shut down the camera.

        Call this when your program is finished with the camera:

            camera.close()
        """

        # If we've already closed it, there is nothing to do.
        if self._closed:
            return


        try:

            # =================================================================
            # 1. TELL THE CAMERA TO STOP ACQUIRING
            # =================================================================
            if self.remote_nodemap is not None:

                try:
                    self.remote_nodemap.FindNode(
                        "AcquisitionStop"
                    ).Execute()

                except Exception:
                    # If acquisition wasn't running, don't worry.
                    pass


            # =================================================================
            # 2. STOP THE DATA STREAM
            # =================================================================
            if self.data_stream is not None:

                try:

                    self.data_stream.StopAcquisition(
                        peak.AcquisitionStopMode_Default
                    )

                except Exception:
                    pass


                # =============================================================
                # 3. CLEAR THE REMAINING BUFFERS
                # =============================================================
                #
                # Discard any buffers still sitting in the acquisition queue.
                #
                try:

                    self.data_stream.Flush(
                        peak.DataStreamFlushMode_DiscardAll
                    )

                except Exception:
                    pass


                # =============================================================
                # 4. RELEASE THE BUFFERS
                # =============================================================
                #
                # We no longer need the memory that we allocated for
                # camera images.
                #
                try:

                    for buffer in self.data_stream.AnnouncedBuffers():

                        self.data_stream.RevokeBuffer(buffer)

                except Exception:
                    pass


            # =================================================================
            # 5. UNLOCK TRANSPORT PARAMETERS
            # =================================================================
            if self.remote_nodemap is not None:

                try:

                    self.remote_nodemap.FindNode(
                        "TLParamsLocked"
                    ).SetValue(0)

                except Exception:
                    pass


        finally:

            # Mark the camera as closed so that snap() cannot be called
            # accidentally afterwards.
            self._closed = True

            # Finally shut down the IDS peak library.
            peak.Library.Close()


    # =========================================================================
    # AUTOMATIC CLEANUP
    # =========================================================================

    def __del__(self):

        # Python calls this when the object is being destroyed.
        #
        # This is just an additional safety net in case the user forgot
        # to explicitly call camera.close().
        try:
            self.close()

        except Exception:
            pass
