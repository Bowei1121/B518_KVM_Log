"""Legacy line replies for the offline Ticket 16 evidence tools.

The TCP server speaks JSON 1.0; nothing on the wire uses this format.
"""


def tcp_round_reply(decision):
    if decision.kind == "frozen" or decision.reason == "frame_frozen":
        return "error:frame_frozen\r\n"
    if decision.kind == "taken":
        body = ",".join("{}::{}".format(slot, status)
                        for slot, status in decision.results)
        return "action_done,{}\r\n".format(body)
    if decision.kind == "paused":
        return "action_paused,review\r\n"
    if decision.kind == "already_taken":
        return "action_waiting,already_taken\r\n"
    if decision.kind == "unknown":
        return "action_waiting,unknown,{}\r\n".format(decision.reason)
    return "action_waiting,{}\r\n".format(decision.reason or "monitoring")
