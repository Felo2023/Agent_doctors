import { tool } from "@opencode-ai/plugin"
import { readFile } from "node:fs/promises"
import { join } from "node:path"

export default tool({
  description: "Ask one targeted colleague question through the bound fictional evaluation bus. Only available during the question round; never for real cases.",
  args: {
    recipient: tool.schema.enum(["opinion-opus", "opinion-astra", "challenger"]),
    body: tool.schema.string().min(1).max(1200),
  },
  async execute(args, context) {
    const binding = JSON.parse(await readFile(join(context.directory, ".eval-mail-binding.json"), "utf8")) as {
      url: string; role_token: string; case_id: string; case_hash: string; session_id: string
    }
    const url = new URL(binding.url)
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || !url.port ||
        binding.session_id !== context.sessionID || !context.messageID || !/^SYNTH-\d{3,6}$/.test(binding.case_id)) {
      throw new Error("Synthetic mail binding does not match this session")
    }
    const response = await fetch(`${url.origin}/question`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${binding.role_token}` },
      body: JSON.stringify({
        case_id: binding.case_id, case_hash: binding.case_hash,
        session_id: context.sessionID, recipient: args.recipient, body: args.body,
        dedupe_key: `${context.messageID}:${args.recipient}`,
      }),
      signal: AbortSignal.timeout(10000),
    })
    if (!response.ok) throw new Error(`Question rejected (${response.status}); do not retry with another colleague.`)
    const receipt = await response.json() as { question_id: string }
    return `Question ${receipt.question_id} queued for the reply round. Do not wait or send another question; finish your requested JSON position.`
  },
})
