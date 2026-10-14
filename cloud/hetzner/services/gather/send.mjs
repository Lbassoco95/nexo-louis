// Envía eventos a la Smart Object "Bot Status Monitor" de Gather.
// Entrada (stdin, JSON): {"state":"on|working|question|alert|off",
//                         "activity":[{"id","text"}], "remove":["id"], "ping":false}
// Env: GATHER_STATUS_URL, GATHER_STATUS_SECRET (whsec_…)
import { createWebhookObjectClient, secretFromEnv } from "@gathertown/webhook-object-sdk"

const chunks = []
for await (const c of process.stdin) chunks.push(c)
const job = JSON.parse(Buffer.concat(chunks).toString() || "{}")

const obj = createWebhookObjectClient({
  url: process.env.GATHER_STATUS_URL,
  secret: secretFromEnv("GATHER_STATUS_SECRET"),
})

if (job.ping) {
  console.log(JSON.stringify(await obj.ping()))
  process.exit(0)
}
if (job.state) await obj.send("status.set", { state: job.state })
for (const a of job.activity ?? []) await obj.send("activity.add", { id: a.id, text: a.text.slice(0, 500) })
for (const id of job.remove ?? []) await obj.send("activity.remove", { id })
console.log("ok")
