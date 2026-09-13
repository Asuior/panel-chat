from functools import wraps
import glob
from pathlib import Path
from magicalimport import import_module

tools_dict_permanent = {}
tools_dict_demand = {}
def tool_resigter(name,load_type="permanent"):
    def decorator(func):
        if load_type == "permanent":
            tools_dict_permanent[name] = func
        else:
            tools_dict_demand[name] = func
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorator


def import_tools(load_type="permanent"):
    current_dir = str(Path(__file__).parent)
    file_list = glob.glob(current_dir+"/tools*/**/*.py",recursive=True)
    for i in file_list:
        import_module(i)
    if load_type == "permanent":
        return tools_dict_permanent
    elif load_type == "demand":
        return tools_dict_demand
    else:
        return tools_dict_demand | tools_dict_permanent
