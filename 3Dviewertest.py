import trimesh, pyvista as pv
m = trimesh.load("model.3mf", force="mesh")
p = pv.Plotter()
p.add_mesh(pv.wrap(m), smooth_shading=True, color="white")
p.add_light(pv.Light(position=(1, 0, 0.3), light_type="scene light"))
p.show()