// Envía eventos a una Smart Object de Gather (Bot Status Monitor o Inbox).
// Entrada (stdin, JSON): {"target":"status"|"inbox",
//                         "state":"on|working|question|alert|off"   (status),
//                         "count":12                                 (inbox),
//                         "activity":[{"id","text"}], "remove":["id"], "ping":false}
// Env por objeto: GATHER_<TARGET>_URL, GATHER_<TARGET>_SECRET (whsec_…)
import { createWebhookObjectClient, secretFromEnv } from "@gathertown/webhook-object-sdk"

const chunks = []
for await (const c of process.stdin) chunks.push(c)
const job = JSON.parse(Buffer.concat(chunks).toString() || "{}")

const target = (job.target ?? "status").toUpperCase()
const obj = createWebhookObjectClient({
  url: process.env[`GATHER_${target}_URL`],
  secret: secretFromEnv(`GATHER_${target}_SECRET`),
})

if (job.ping) {
  console.log(JSON.stringify(await obj.ping()))
  process.exit(0)
}
if (typeof job.count === "number") await obj.send("counter.set", { count: job.count })
if (job.state) await obj.send("status.set", { state: job.state })
for (const a of job.activity ?? []) await obj.send("activity.add", { id: a.id, text: a.text.slice(0, 500) })
for (const id of job.remove ?? []) await obj.send("activity.remove", { id })
console.log("ok")
