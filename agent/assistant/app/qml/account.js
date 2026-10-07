// SPDX-License-Identifier: GPL-2.0-or-later
// How Codex is signed in, in words (docs/101): the settings, the Codex page and the sign-in page say
// it the same way. Codex tells the kind (chatgpt, apiKey), the ChatGPT plan and the email; not
// whether a ChatGPT sign-in went through the device code or the browser, and billing doesn't
// depend on it, so a ChatGPT sign-in is named by its plan.
// tr is the page's i18nc, passed in (a library has no context to find it in).
.pragma library

function plan(tr, planType) {
    switch (planType) {
    case "free": return "Free"
    case "go": return "Go"
    case "plus": return "Plus"
    case "pro": return "Pro"
    case "team": return "Team"
    case "business": return "Business"
    case "enterprise": return "Enterprise"
    case "edu": return "Edu"
    case undefined: case null: case "": case "unknown": return ""
    default: return planType.charAt(0).toUpperCase() + planType.slice(1)
    }
}

function kind(account) {
    if (!account) return "none"
    return account.type === "chatgpt" ? "chatgpt" : account.type === "apiKey" ? "apiKey" : account.type ? "other" : "none"
}

// "ChatGPT plan (Team)", "API key", "Not signed in"
function label(tr, account, status) {
    if (status === "not-installed") return tr("@info how Codex is signed in", "Not installed")
    if (status === "offline" || status === "unknown") return tr("@info", "Cannot confirm the sign-in right now")
    switch (kind(account)) {
    case "chatgpt": {
        const name = plan(tr, account.planType)
        return name ? tr("@info how Codex is signed in; %1 is the plan's name", "ChatGPT plan (%1)", name)
                    : tr("@info how Codex is signed in", "ChatGPT plan")
    }
    case "apiKey": return tr("@info how Codex is signed in", "API key")
    case "other": return account.type
    default: return tr("@info how Codex is signed in", "Not signed in")
    }
}

// Who pays for the agent's tasks.
function billing(tr, account) {
    switch (kind(account)) {
    case "chatgpt": return tr("@info", "The Agent's tasks count against your ChatGPT plan.")
    case "apiKey": return tr("@info", "The Agent's tasks are billed to the OpenAI API by use, not to a ChatGPT plan.")
    default: return tr("@info", "The Agent needs Codex to be signed in before it can work.")
    }
}
