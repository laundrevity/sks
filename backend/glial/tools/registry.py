from pathlib import Path
import importlib.util
import inspect


_PYTHON_TO_JSON_TYPE = {
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "dict": "object",
    "str": "string"
}


def tool(description, **arg_descriptions):
    """
    Decorator that adds JSON .schema attribute for callable functinos
    """
    def inner(func):
        schema = {
            "type": "function",
            "name": func.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            },
            "strict": True
        }
        descriptions = list(arg_descriptions.values())
        annotations = list(inspect.signature(func).parameters.values())

        for desc, anno in list(zip(descriptions, annotations)):
            schema["parameters"]["properties"][anno.name] = {
                "type": _PYTHON_TO_JSON_TYPE[anno.annotation.__name__],
                "description": desc
            }
            schema["parameters"]["required"].append(anno.name)

        func.schema = schema
        return func
    return inner

def custom(description):
    """
    Decorator that adds JSON .custom attribute for callable custom tools
    """
    def inner(func):
        func.custom = {
            "type": "custom",
            "name": func.__name__,
            "description": description
        }
        return func
    return inner


def gather_tools(ref):
    schemas = []
    tools = {}
    current_file, current_dir = Path(__file__).name, Path(__file__).parent
    for py_file in current_dir.glob("*.py"):
        if py_file.name == current_file:
            continue

        module_name = "tools." + py_file.stem
        spec = importlib.util.spec_from_file_location(module_name, py_file)

        module = importlib.util.module_from_spec(spec)  # type: ignore
        spec.loader.exec_module(module)  # type: ignore

        # find functions with a .schema or .custom attribute
        for name, obj in vars(module).items():
            if inspect.isfunction(obj) or inspect.iscoroutinefunction(obj):
                if hasattr(obj, "schema"):
                    schemas.append(obj.schema)  # type: ignore
                    tools[name] = obj
                elif hasattr(obj, "custom"):
                    obj.ref = ref
                    schemas.append(obj.custom)
                    tools[name] = obj

    return schemas, tools

