// Cloudflare Workers Scheduler - Auto-deployed (v1.1)
const DEFAULT_TIMEZONE = "Asia/Taipei"
const DEFAULT_REF = "main"
const DEFAULT_CONFIG_PATH = ".github/runtime/scheduler_config.json"

export default {
  async scheduled(controller, env, ctx) {
    const now = new Date(controller.scheduledTime || Date.now())
    ctx.waitUntil(runScheduler(now, env))
  },

  async fetch(request, env) {
    const url = new URL(request.url)
    const nowText = url.searchParams.get("now")
    const now = nowText ? new Date(nowText) : new Date()

    if (Number.isNaN(now.getTime())) {
      return jsonResponse({ ok: false, error: "Invalid now parameter" }, 400)
    }

    try {
      const result = await evaluateSchedule(now, env, false)
      return jsonResponse(result)
    } catch (error) {
      return jsonResponse(
        {
          ok: false,
          error: error instanceof Error ? error.message : String(error),
        },
        500
      )
    }
  },
}

async function runScheduler(now, env) {
  const result = await evaluateSchedule(now, env, true)
  console.log(JSON.stringify(result))
}

async function evaluateSchedule(now, env, shouldDispatch) {
  const config = await loadSchedulerConfig(env)
  const timezone = String(config.timezone || DEFAULT_TIMEZONE)
  const zoned = getZonedNow(now, timezone)

  const dueJobs = []
  const invalidJobs = []

  for (const rawJob of Array.isArray(config.jobs) ? config.jobs : []) {
    try {
      const job = normalizeJob(rawJob)
      if (isJobDue(job, zoned)) {
        dueJobs.push(job)
      }
    } catch (error) {
      invalidJobs.push({
        job: rawJob && typeof rawJob === "object" ? rawJob.name || "" : "",
        error: error instanceof Error ? error.message : String(error),
      })
    }
  }

  const dispatched = []
  if (shouldDispatch) {
    for (const job of dueJobs) {
      dispatched.push(await dispatchWorkflow(job, zoned, env))
    }
  }

  return {
    ok: true,
    dispatch: shouldDispatch,
    timezone,
    now_utc: now.toISOString(),
    now_zoned: zoned.dateTime,
    due_jobs: dueJobs.map((job) => summarizeJob(job)),
    invalid_jobs: invalidJobs,
    dispatched,
  }
}

async function loadSchedulerConfig(env) {
  const owner = requireEnv(env, "GITHUB_OWNER")
  const repo = requireEnv(env, "GITHUB_REPO")
  const configPath = String(env.CONFIG_PATH || DEFAULT_CONFIG_PATH)
  const configRef = String(env.CONFIG_REF || env.GITHUB_REF || DEFAULT_REF)
  const token = requireEnv(env, "GITHUB_TOKEN")

  const response = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/contents/${encodeURIComponentPath(configPath)}?ref=${encodeURIComponent(configRef)}`,
    {
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "User-Agent": "blablalink-cloudflare-scheduler",
      },
    }
  )

  if (!response.ok) {
    const body = await response.text()
    throw new Error(`Failed to load scheduler config: ${response.status} ${body}`)
  }

  const payload = await response.json()
  const content = decodeBase64Utf8(String(payload.content || ""))
  let parsed
  try {
    parsed = JSON.parse(content)
  } catch (error) {
    throw new Error(`Invalid scheduler config JSON: ${error instanceof Error ? error.message : String(error)}`)
  }

  if (!parsed || typeof parsed !== "object") {
    throw new Error("Scheduler config must be a JSON object")
  }

  return parsed
}

function normalizeJob(rawJob) {
  if (!rawJob || typeof rawJob !== "object") {
    throw new Error("Job must be an object")
  }

  const name = String(rawJob.name || "").trim()
  const enabled = Boolean(rawJob.enabled)
  const type = String(rawJob.type || "").trim().toLowerCase()
  const workflow = String(rawJob.workflow || "").trim()
  const ref = String(rawJob.ref || DEFAULT_REF).trim() || DEFAULT_REF
  const inputs = normalizeInputs(rawJob.inputs)

  if (!name) {
    throw new Error("Job name is required")
  }
  if (!workflow) {
    throw new Error(`Job ${name} is missing workflow`)
  }
  if (!["daily", "once", "interval"].includes(type)) {
    throw new Error(`Job ${name} has unsupported type: ${type}`)
  }

  const time = parseTime(rawJob.time, name)
  const daysOfWeek = normalizeDaysOfWeek(rawJob.days_of_week, name)
  const date = type === "once" ? parseDate(rawJob.date, name) : ""
  
  let startDate = ""
  let endDate = ""
  let startDateTime = ""
  let endDateTimeInclusive = ""
  let intervalMinutes = 5
  if (type === "interval") {
    intervalMinutes = Number(rawJob.interval_minutes || 5)
    if (isNaN(intervalMinutes) || intervalMinutes < 1) {
      intervalMinutes = 5
    }
    startDate = parseDate(rawJob.start_date, name)
    endDate = addDaysToDateText(startDate, 1)
    startDateTime = `${startDate}T${time.text}:00`
    endDateTimeInclusive = `${endDate}T${time.text}:00`
  }

  return {
    name,
    enabled,
    type,
    workflow,
    ref,
    inputs,
    time,
    daysOfWeek,
    date,
    startDate,
    endDate,
    startDateTime,
    endDateTimeInclusive,
    intervalMinutes,
  }
}

function normalizeInputs(rawInputs) {
  if (rawInputs == null) {
    return {}
  }
  if (typeof rawInputs !== "object" || Array.isArray(rawInputs)) {
    throw new Error("Job inputs must be an object")
  }

  const inputs = {}
  for (const [key, value] of Object.entries(rawInputs)) {
    const cleanKey = String(key || "").trim()
    if (!cleanKey) {
      continue
    }
    inputs[cleanKey] = value == null ? "" : String(value)
  }
  return inputs
}

function normalizeDaysOfWeek(rawDays, jobName) {
  if (rawDays == null) {
    return null
  }
  if (!Array.isArray(rawDays)) {
    throw new Error(`Job ${jobName} days_of_week must be an array`)
  }

  const result = []
  for (const value of rawDays) {
    const day = Number(value)
    if (!Number.isInteger(day) || day < 0 || day > 6) {
      throw new Error(`Job ${jobName} has invalid day_of_week: ${value}`)
    }
    result.push(day)
  }
  return result
}

function parseTime(rawTime, jobName) {
  const text = String(rawTime || "").trim()
  const match = /^(\d{2}):(\d{2})$/.exec(text)
  if (!match) {
    throw new Error(`Job ${jobName} has invalid time: ${rawTime}`)
  }

  const hour = Number(match[1])
  const minute = Number(match[2])
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) {
    throw new Error(`Job ${jobName} has out-of-range time: ${rawTime}`)
  }

  return { text, hour, minute }
}

function parseDate(rawDate, jobName) {
  const text = String(rawDate || "").trim()
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) {
    throw new Error(`Job ${jobName} has invalid date: ${rawDate}`)
  }
  return text
}

function isJobDue(job, zoned) {
  if (!job.enabled) {
    return false
  }

  if (job.type === "daily") {
    if (job.time.hour !== zoned.hour || job.time.minute !== zoned.minute) {
      return false
    }
    if (job.daysOfWeek && !job.daysOfWeek.includes(zoned.weekday)) {
      return false
    }
    return true
  }

  if (job.type === "once") {
    if (job.time.hour !== zoned.hour || job.time.minute !== zoned.minute) {
      return false
    }
    return job.date === zoned.date
  }

  if (job.type === "interval") {
    const currentDateTimeMinute = `${zoned.date}T${pad2(zoned.hour)}:${pad2(zoned.minute)}:00`
    if (currentDateTimeMinute < job.startDateTime || currentDateTimeMinute > job.endDateTimeInclusive) {
      return false
    }

    const elapsedMinutes = diffMinutesBetweenDateTimes(job.startDateTime, currentDateTimeMinute)
    if (elapsedMinutes < 0) {
      return false
    }

    return elapsedMinutes % job.intervalMinutes === 0
  }

  return false
}

async function dispatchWorkflow(job, zoned, env) {
  const owner = requireEnv(env, "GITHUB_OWNER")
  const repo = requireEnv(env, "GITHUB_REPO")
  const token = requireEnv(env, "GITHUB_TOKEN")

  const inputs = {
    ...job.inputs,
    scheduler_source: "cloudflare-worker",
    scheduler_job_name: job.name,
    scheduler_fire_time: zoned.dateTime,
  }

  const response = await fetch(
    `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${encodeURIComponent(job.workflow)}/dispatches`,
    {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        "User-Agent": "blablalink-cloudflare-scheduler",
      },
      body: JSON.stringify({
        ref: job.ref,
        inputs,
      }),
    }
  )

  const ok = response.status === 204 || response.status === 201 || response.status === 200
  const body = ok ? "" : await response.text()

  return {
    ...summarizeJob(job),
    ok,
    status: response.status,
    response: body,
  }
}

function summarizeJob(job) {
  const summary = {
    name: job.name,
    type: job.type,
    workflow: job.workflow,
    ref: job.ref,
    time: job.time.text,
    date: job.date,
    days_of_week: job.daysOfWeek,
    inputs: job.inputs,
  }
  if (job.type === "interval") {
    summary.start_date = job.startDate
    summary.end_date = job.endDate
    summary.start_at = job.startDateTime
    summary.end_at_inclusive = job.endDateTimeInclusive
    summary.interval_minutes = job.intervalMinutes
    summary.timezone = "Asia/Taipei"
  }
  return summary
}

function getZonedNow(now, timeZone) {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    weekday: "short",
  })
  const parts = formatter.formatToParts(now)
  const map = {}
  for (const part of parts) {
    map[part.type] = part.value
  }

  const weekdayMap = {
    Sun: 0,
    Mon: 1,
    Tue: 2,
    Wed: 3,
    Thu: 4,
    Fri: 5,
    Sat: 6,
  }

  const year = Number(map.year)
  const month = Number(map.month)
  const day = Number(map.day)
  const hour = Number(map.hour)
  const minute = Number(map.minute)
  const second = Number(map.second)
  const weekday = weekdayMap[map.weekday]

  if ([year, month, day, hour, minute, second].some((value) => !Number.isInteger(value))) {
    throw new Error(`Failed to compute zoned time for ${timeZone}`)
  }

  return {
    timeZone,
    date: `${map.year}-${map.month}-${map.day}`,
    dateTime: `${map.year}-${map.month}-${map.day}T${map.hour}:${map.minute}:${map.second}`,
    year,
    month,
    day,
    hour,
    minute,
    second,
    weekday,
  }
}

function requireEnv(env, key) {
  const value = String(env[key] || "").trim()
  if (!value) {
    throw new Error(`Missing environment variable: ${key}`)
  }
  return value
}

function addDaysToDateText(dateText, days) {
  const [year, month, day] = dateText.split("-").map(Number)
  const utc = new Date(Date.UTC(year, month - 1, day + days))
  return `${utc.getUTCFullYear()}-${pad2(utc.getUTCMonth() + 1)}-${pad2(utc.getUTCDate())}`
}

function diffMinutesBetweenDateTimes(startText, endText) {
  const start = parseDateTimeText(startText)
  const end = parseDateTimeText(endText)
  return Math.floor((end.getTime() - start.getTime()) / 60000)
}

function parseDateTimeText(text) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})$/.exec(String(text || "").trim())
  if (!match) {
    throw new Error(`Invalid datetime text: ${text}`)
  }
  const [, year, month, day, hour, minute, second] = match
  return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hour), Number(minute), Number(second)))
}

function pad2(value) {
  return String(value).padStart(2, "0")
}

function encodeURIComponentPath(path) {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/")
}

function decodeBase64Utf8(value) {
  const clean = value.replace(/\s+/g, "")
  const binary = atob(clean)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return new TextDecoder().decode(bytes)
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
    },
  })
}
