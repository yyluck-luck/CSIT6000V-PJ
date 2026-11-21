import os, importlib.util
os.chdir('./challenge')
spec = importlib.util.spec_from_file_location('ch', 'code.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod.print_flag()