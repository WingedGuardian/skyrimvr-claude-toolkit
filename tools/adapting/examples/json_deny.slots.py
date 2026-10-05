# SLOTS-BEGIN
AGENT_NAME = "toy-json"
STATIC_BLOCK = ('{"toy_decision":"block","reason":"GUARD INERT: adapter failed"}', 0, "")
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")


def map_payload(raw):
    d = json.loads(raw.decode("utf-8"))
    tool, args = d["tool"], d.get("args", {})
    base = {"v": 1, "agent": "toy-json", "event": "pre_tool", "cwd": d.get("cwd", "")}
    if tool == "write_file":
        return dict(base, kind="write", paths=[args["path"]], ops=["update"])
    if tool == "run_shell":
        return dict(base, kind="shell", shell=None, command=args["cmd"])
    return dict(base, kind="other", tool_name=tool)


def render_block(reason):
    return (json.dumps({"toy_decision": "block", "reason": reason}), 0, "")


def render_allow(context):
    return (json.dumps({"toy_decision": "allow", "context": context}), 0, "")


render_ask = None
# SLOTS-END
