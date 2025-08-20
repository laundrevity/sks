import contextlib
import traceback
import json
import io


from glial.tools.registry import custom


@custom(
        "Execute arbitrary Python code. Returns a dictionary with  returncode, stdout and stderr. "
        "Globals and locals are persisted across calls within the same conversation."
)
def code_exec(code: str):
    fout, ferr = io.StringIO(), io.StringIO()
    rc = 0
    with contextlib.redirect_stdout(fout), contextlib.redirect_stderr(ferr):
        try:
            exec(code, code_exec.ref.locals, code_exec.ref.globals)
        except Exception:
            ferr.write(traceback.format_exc())
            rc = 1
    return json.dumps({
        "returncode": rc,
        "stdout": fout.getvalue(),
        "stderr": ferr.getvalue()
    })

