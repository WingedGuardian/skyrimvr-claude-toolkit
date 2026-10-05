# SLOTS-BEGIN
AGENT_NAME = "toy-json"
STATIC_BLOCK = ('{"toy_decision":"block","reason":"GUARD INERT: adapter failed"}', 0, "")
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")
# EVERY tool of your agent that writes or edits a file, and EVERY tool that runs a command, by the agent's own
# names. The same two lists go into profile.json (write_tools, shell_tools) so conformance probes each one.
WRITE_TOOLS = ("write_file",)
SHELL_TOOLS = ("run_shell",)


def map_payload(raw):
    d = json.loads(raw.decode("utf-8"))
    tool, args = d["tool"], d.get("args", {})
    base = {"v": 1, "agent": "toy-json", "event": "pre_tool", "cwd": d.get("cwd", "")}
    if tool in WRITE_TOOLS:
        return dict(base, kind="write", paths=[args["path"]], ops=["update"])
    if tool in SHELL_TOOLS:
        return dict(base, kind="shell", shell=None, command=args["cmd"])
    # UNMAPPED TOOLS ARE NOT GUARDED: anything that reaches this line is sent as kind other, which the engine
    # allows unjudged. Only a tool that neither writes nor runs anything may end up here.
    return dict(base, kind="other", tool_name=tool)


def render_block(reason):
    return (json.dumps({"toy_decision": "block", "reason": reason}), 0, "")


def render_allow(context):
    return (json.dumps({"toy_decision": "allow", "context": context}), 0, "")


render_ask = None
# SLOTS-END
