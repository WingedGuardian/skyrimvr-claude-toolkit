# SLOTS-BEGIN
AGENT_NAME = "toy-exit2"
STATIC_BLOCK = ("", 2, "GUARD INERT: adapter failed")
ASK_PREFIX = ("NEEDS YOUR CONFIRMATION -- this agent cannot ask, so the toolkit refuses instead. Stop, tell "
              "the user what you wanted to run and why; if they agree, they run it themselves. ")
# EVERY tool of your agent that writes or edits a file, and EVERY tool that runs a command, by the agent's own
# names. The same two lists go into profile.json (write_tools, shell_tools) so conformance probes each one.
WRITE_TOOLS = ("edit",)
SHELL_TOOLS = ("sh",)


def map_payload(raw):
    d = json.loads(raw.decode("utf-8"))
    call = d["call"]
    base = {"v": 1, "agent": "toy-exit2", "event": "pre_tool", "cwd": d.get("cwd", "")}
    if call["name"] in WRITE_TOOLS:
        return dict(base, kind="write", paths=[call["file"]], ops=["update"])
    if call["name"] in SHELL_TOOLS:
        return dict(base, kind="shell", shell=None, command=call["cmdline"])
    # UNMAPPED TOOLS ARE NOT GUARDED: anything that reaches this line is sent as kind other, which the engine
    # allows unjudged. Only a tool that neither writes nor runs anything may end up here.
    return dict(base, kind="other", tool_name=call["name"])


def render_block(reason):
    return ("", 2, reason)


def render_allow(context):
    return ("", 0, "")


render_ask = None
# SLOTS-END
