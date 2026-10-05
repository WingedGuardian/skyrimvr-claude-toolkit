# SLOTS-BEGIN
AGENT_NAME = "toy-exit2"
STATIC_BLOCK = ("", 2, "GUARD INERT: adapter failed")
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")


def map_payload(raw):
    d = json.loads(raw.decode("utf-8"))
    call = d["call"]
    base = {"v": 1, "agent": "toy-exit2", "event": "pre_tool", "cwd": d.get("cwd", "")}
    if call["name"] == "edit":
        return dict(base, kind="write", paths=[call["file"]], ops=["update"])
    if call["name"] == "sh":
        return dict(base, kind="shell", shell=None, command=call["cmdline"])
    return dict(base, kind="other", tool_name=call["name"])


def render_block(reason):
    return ("", 2, reason)


def render_allow(context):
    return ("", 0, "")


render_ask = None
# SLOTS-END
