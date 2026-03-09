# GETAI - AI-First IT Company Infrastructure

## Owner
- Name: Asranov Maksat
- Email: asranov.maksat@gmail.com
- Location: Bishkek, Kyrgyzstan
- Domain: get.kg
- GitHub: github.com/Asranov-Get

## Server
- Provider: Hetzner Cloud
- Plan: CPX22 (2 CPU, 8GB RAM, 80GB SSD)
- IP: 46.224.117.211
- OS: Ubuntu 24.04 LTS
- Domain: ai.get.kg (A-record added)

## Architecture

### Services (Docker)
- ai-company-bot-1: Telegram bot (GETAI v4, Python/aiogram)
- ai-company-db-1: PostgreSQL + pgvector (memory)
- getai-web: Nginx Alpine (static website)

### Local AI (Ollama, 0 tokens)
- qwen2.5-coder:7b (4.7GB) - code generation (PRIMARY)
- qwen2.5:3b (1.9GB) - general tasks
- llama3.2:1b (1.3GB) - fast/routing
- nomic-embed-text (274MB) - vector search

### AI Cascade (all free)
1. Ollama local (unlimited, 0 tokens)
2. Groq (14,400 req/day free)
3. Cerebras (1,000 req/day free)
4. OpenRouter (1,000 req/day free) - KEY NEEDED
5. Google Gemini (1,500 req/day free) - KEY NEEDED

### Database Tables (PostgreSQL)
- conversations: chat history with user_id, role, content, model_used
- conversation_summaries: compressed summaries every 20 messages
- knowledge_base: company docs with vector embeddings
- tasks_queue: autonomous task queue (pending/in_progress/done/failed)
- agent_logs: all agent actions log
- projects: project registry
- token_usage: daily token counter per provider

### Telegram Bot Commands
- /start - welcome + local AI status
- /status - server health
- /docker - container status
- /sh <cmd> - execute shell command
- /search <query> - web search (DuckDuckGo + Tavily)
- /fetch <url> - fetch and summarize webpage
- /task <desc> - add task to queue
- /tasks - list tasks
- /agent <task> - run 7 agents (Orchestrator/Manager/Architect/Backend/Frontend/DevOps/QA)
- /plan <task> - autonomous execution (write files, git push, build, restart)
- /jira [list|task] - Jira integration
- /memory - memory stats
- /usage - token usage today
- /models - available AI models

### Agents
- Orchestrator: routes tasks to correct agent
- Manager: writes TZ (technical specification)
- Architect: designs solutions
- Backend: Python/FastAPI code
- Frontend: React/Next.js/Tailwind
- DevOps: Docker/Nginx/bash
- QA: reviews code, writes tests

### Heartbeat
- Runs every 30 minutes
- Picks pending tasks from tasks_queue
- Executes using local AI (0 tokens)
- Reports results to owner via Telegram

### Website
- URL: http://46.224.117.211 (soon https://ai.get.kg)
- Stack: Next.js 16 + Tailwind CSS
- Theme: Dark with indigo/cyan gradients
- Sections: Hero, Services, How We Work, Tech Stack, AI Core, Contact
- Brand: GETAI (not GETAI Core)

## API Keys (.env)
- TG_BOT_TOKEN: 7929185700:AAGi... (Telegram)
- GROQ_API_KEY: gsk_tr01... (Groq)
- CEREBRAS_API_KEY: csk-jfyf... (Cerebras)
- OPENROUTER_API_KEY: INCOMPLETE - need full sk-or-v1-...
- GEMINI_API_KEY: INCOMPLETE - need full AIza...
- TAVILY_API_KEY: tvly-dev-2Dcr... (web search)
- GITHUB_TOKEN: ghp_Jzkj... (classic, repo scope)
- JIRA_TOKEN: ATCTT3... (Atlassian)
- JIRA_EMAIL: asranov.maksat@gmail.com
- JIRA_URL: https://asranovmaksat.atlassian.net

## GitHub Repos
- Asranov-Get/getai-website - corporate site
- Asranov-Get/getai-core-bot - telegram bot v4
- Asranov-Get/getai-agents - multi-agent system (planned)

## TODO (next session)
1. Configure SSL (Caddy) for ai.get.kg
2. Get full OpenRouter key (sk-or-v1-...)
3. Get full Gemini key (AIza...)
4. Activate Jira Software in Atlassian admin
5. Rename bot to "GETAI Assistant" in BotFather
6. Add DNS records: get.kg and www.get.kg
7. Improve website design (animations, images)
8. Add self-improving planning agent
9. Add cron-based monitoring alerts

## How to Continue
In new OpenCode chat, say:
"Read /opt/ai-company/PROJECT_SUMMARY.md on server 46.224.117.211 and continue"
SSH key: ~/.ssh/id_rsa_opencode
