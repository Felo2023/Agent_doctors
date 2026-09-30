import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Read the bound, human-reviewed SYNTH-* case snapshot for the current synthetic run. It is not a diagnosis-ready record.",
  args: {},
  async execute(_args, context) {
    const url = process.env.PROTOTYPE_BUS_URL
    const token = process.env.PROTOTYPE_ROLE_TOKEN
    if (!url || !token || !url.startsWith("http://127.0.0.1:") || !context.sessionID) {
      throw new Error("Synthetic case bus is not configured")
    }
    const response = await fetch(`${url}/case`, { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) throw new Error(`Reviewed case unavailable (${response.status})`)
    const content = await response.json() as { case_id: string; version: number; status: string; ready_for_diagnosis: boolean }
    if (content.case_id !== process.env.PROTOTYPE_CASE_ID || content.version !== Number(process.env.PROTOTYPE_CASE_VERSION)) {
      throw new Error("Case snapshot does not match this doctor's assigned case version")
    }
    return JSON.stringify(content)
  },
})
