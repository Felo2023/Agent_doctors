import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Send a letter to the other participant in the bound SYNTH-* case run. Never use for real cases.",
  args: {
    recipient: tool.schema.enum(["doctor-a", "doctor-b"]),
    body: tool.schema.string().min(1).max(2000),
  },
  async execute(args, context) {
    const url = process.env.PROTOTYPE_BUS_URL
    const token = process.env.PROTOTYPE_ROLE_TOKEN
    if (!url || !token || !url.startsWith("http://127.0.0.1:")) {
      throw new Error("Synthetic bus is not configured")
    }
    if (!context.sessionID || !context.messageID) throw new Error("Missing OpenCode session context")
    const response = await fetch(`${url}/mail`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({
        case_id: process.env.PROTOTYPE_CASE_ID ?? "SYNTH-001",
        case_version: process.env.PROTOTYPE_CASE_VERSION ? Number(process.env.PROTOTYPE_CASE_VERSION) : undefined,
        sender_session: context.sessionID,
        recipient: args.recipient,
        body: args.body,
        dedupe_key: `${context.messageID}:${args.recipient}`,
      }),
    })
    if (!response.ok) throw new Error(`Bus rejected mail with status ${response.status}`)
    const receipt = await response.json() as { mail_id: number; state: string }
    return `Mail ${receipt.mail_id} accepted (${receipt.state}). Do not wait synchronously for a reply.`
  },
})
