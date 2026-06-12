def examine_os_error(e: OSError) -> str:

    err_string = f"{getattr(e, '__module__', 'none')}.{repr(e)} "
    err_string += str(e) if str(e) else ""
    if e.strerror:
        err_string += f": {e.strerror}"
    if e.errno:
        err_string += f"  (Code: {e.errno})"
    if e.__context__:
        full_context = (
            f"{getattr(e.__context__, '__module__', 'none')}.{repr(e.__context__)}"
        )
        err_string += f"\n  Occurred while handling: {full_context}"
    if e.__cause__:
        full_cause = f"{getattr(e.__cause__, '__module__', 'none')}.{repr(e.__cause__)}"
        err_string += f"\n  Caused by: {full_cause}"

    return err_string
