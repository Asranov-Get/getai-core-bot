import asyncio,logging,os,subprocess,json,aiohttp,asyncpg,base64
from aiogram import Bot,Dispatcher,types
from aiogram.filters import CommandStart,Command
from aiogram.enums import ParseMode
from openai import AsyncOpenAI
logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)
BOT_TOKEN=os.getenv("TG_BOT_TOKEN")
GROQ_KEY=os.getenv("GROQ_API_KEY")
OLLAMA_BASE=os.getenv("OLLAMA_HOST","http://172.17.0.1:11434")
DB_USER=os.getenv("POSTGRES_USER","ai_admin")
DB_PASS=os.getenv("POSTGRES_PASSWORD","secure_ai_pass_2026")
DB_NAME=os.getenv("POSTGRES_DB","ai_memory")
DB_HOST=os.getenv("DB_HOST","db")
JIRA_URL=os.getenv("JIRA_URL","https://asranovmaksat.atlassian.net")
JIRA_EMAIL=os.getenv("JIRA_EMAIL","")
JIRA_TOKEN_J=os.getenv("JIRA_TOKEN","")
JIRA_PROJECT=os.getenv("JIRA_PROJECT","GETAI")
bot=Bot(token=BOT_TOKEN)
dp=Dispatcher()
local_ai=AsyncOpenAI(api_key="ollama",base_url=OLLAMA_BASE+"/v1")
groq_ai=AsyncOpenAI(api_key=GROQ_KEY,base_url="https://api.groq.com/openai/v1")
db_pool=None
OWNER_ID=None
LOCAL_MAIN="qwen2.5:3b"
LOCAL_FAST="llama3.2:1b"
GROQ_FAST="llama-3.1-8b-instant"
GROQ_SMART="llama-3.3-70b-versatile"
SIMPLE_KW=["привет","как дела","спасибо","ок","да","нет","hi","hello"]
COMPLEX_KW=["архитект","разработ","задач","проект","план","систем","код","база","напиши","создай"]

def classify(text):
    t=text.lower()
    if text.startswith("/"): return "command"
    if len(t)<20 and any(k in t for k in SIMPLE_KW): return "simple"
    if any(k in t for k in COMPLEX_KW): return "smart"
    return "medium"

async def get_local_models():
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(OLLAMA_BASE+"/api/tags",timeout=aiohttp.ClientTimeout(total=3)) as r:
                if r.status==200: return [m["name"] for m in (await r.json()).get("models",[])]
    except: pass
    return []

async def ask_ai(messages,prefer_local=True,fast=False):
    if prefer_local:
        models=await get_local_models()
        lm=LOCAL_MAIN if LOCAL_MAIN in models else LOCAL_FAST
        if lm in models:
            try:
                resp=await local_ai.chat.completions.create(model=lm,messages=messages,temperature=0.3,max_tokens=2048)
                return resp.choices[0].message.content,"local:"+lm
            except Exception as e: log.warning(f"Local: {e}")
    model=GROQ_FAST if fast else GROQ_SMART
    try:
        resp=await groq_ai.chat.completions.create(model=model,messages=messages,temperature=0.3,max_tokens=2048)
        return resp.choices[0].message.content,"groq:"+model
    except Exception as e: log.error(f"Groq: {e}"); return "Error: "+str(e),"error"

async def get_db():
    global db_pool
    if db_pool is None:
        db_pool=await asyncpg.create_pool(host=DB_HOST,user=DB_USER,password=DB_PASS,database=DB_NAME,min_size=1,max_size=5)
    return db_pool

async def save_msg(uid,role,content,model=None):
    try:
        pool=await get_db()
        async with pool.acquire() as c: await c.execute("INSERT INTO conversations (user_id,role,content,model_used) VALUES (,,,)",uid,role,str(content)[:4000],model)
    except Exception as e: log.error(f"DB: {e}")

async def get_history(uid,limit=8):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            summary=await c.fetchval("SELECT summary FROM conversation_summaries WHERE user_id= ORDER BY created_at DESC LIMIT 1",uid)
            rows=await c.fetch("SELECT role,content FROM conversations WHERE user_id= ORDER BY created_at DESC LIMIT ",uid,limit)
        msgs=[{"role":r["role"],"content":r["content"]} for r in reversed(rows)]
        if summary: msgs.insert(0,{"role":"system","content":"Context: "+summary})
        return msgs
    except Exception as e: log.error(f"Hist: {e}"); return []

async def maybe_summarize(uid):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            cnt=await c.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id=",uid)
            if cnt>0 and cnt%20==0:
                rows=await c.fetch("SELECT role,content FROM conversations WHERE user_id= ORDER BY created_at DESC LIMIT 20",uid)
                hist=chr(10).join([r["role"]+": "+r["content"][:150] for r in reversed(rows)])
                s,_=await ask_ai([{"role":"user","content":"Compress to 3 sentences: "+hist}],True,True)
                if s: await c.execute("INSERT INTO conversation_summaries (user_id,summary,message_count) VALUES (,,)",uid,s,cnt)
    except Exception as e: log.error(f"Sum: {e}")

async def log_action(agent,action,details,status="ok"):
    try:
        pool=await get_db()
        async with pool.acquire() as c: await c.execute("INSERT INTO agent_logs (agent,action,details,status) VALUES (,,,)",agent,action[:200],str(details)[:1000],status)
    except Exception as e: log.error(f"Log: {e}")

SYS="Ты GETAI Assistant - AI-ядро get.kg Бишкек. Управляешь сервером и командой. Кратко на русском. Markdown."

async def smart_answer(uid,text):
    level=classify(text); fast=level in("simple","medium")
    history=await get_history(uid,6 if fast else 8)
    msgs=[{"role":"system","content":SYS}]+history+[{"role":"user","content":text}]
    return await ask_ai(msgs,prefer_local=True,fast=fast)

AGENT_SYS={
    "orchestrator":"Orchestrator GETAI: choose agent (manager/architect/backend/frontend/devops/qa). Reply ONLY JSON: {agent: name}",
    "manager":"Project Manager GETAI. Write TZ: goal, requirements, acceptance criteria. Russian markdown.",
    "architect":"Software Architect GETAI. Design: stack, architecture, components, risks. Markdown.",
    "backend":"Backend Developer GETAI. Python/FastAPI. Working code with comments.",
    "frontend":"Frontend Developer GETAI. React/Next.js/Tailwind. Adaptive responsive components.",
    "devops":"DevOps GETAI. Ubuntu/Docker/Nginx. Bash scripts, docker-compose. Safe and concrete.",
    "qa":"QA Engineer GETAI. Find bugs, write pytest tests. Specific actionable comments.",
}

async def run_agent(name,task):
    msgs=[{"role":"system","content":AGENT_SYS.get(name,AGENT_SYS["manager"])},{"role":"user","content":task}]
    res,model=await ask_ai(msgs,True,False)
    await log_action(name,task[:80],res[:200])
    log.info(f"Agent {name} -> {model}")
    return res

async def orchestrate(task):
    raw,_=await ask_ai([{"role":"system","content":AGENT_SYS["orchestrator"]},{"role":"user","content":task}],True,True)
    try:
        rc=raw.strip(); start=rc.find("{")
        if start>=0: agent=json.loads(rc[start:rc.find("}")+1]).get("agent","manager")
        else: agent="manager"
    except: agent="manager"
    tz=await run_agent("manager",task)
    result=await run_agent(agent,task)
    qa=await run_agent("qa","Review: "+result[:400])
    n=chr(10)
    return "*Agent:* "+n+n+"*TZ:*"+n+tz[:500]+n+n+"*Result:*"+n+result[:500]+n+n+"*QA:*"+n+qa[:300]

async def jira_create(title,desc):
    if not JIRA_EMAIL or not JIRA_TOKEN_J: return "Jira: add JIRA_EMAIL + JIRA_TOKEN to .env"
    auth=base64.b64encode((JIRA_EMAIL+":"+JIRA_TOKEN_J).encode()).decode()
    hdr={"Authorization":"Basic "+auth,"Content-Type":"application/json","Accept":"application/json"}
    body={"fields":{"project":{"key":JIRA_PROJECT},"summary":title[:255],"description":{"type":"doc","version":1,"content":[{"type":"paragraph","content":[{"type":"text","text":desc[:3000]}]}]},"issuetype":{"name":"Task"}}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(JIRA_URL+"/rest/api/3/issue",json=body,headers=hdr,timeout=aiohttp.ClientTimeout(total=15)) as r:
                d=await r.json()
                if r.status in(200,201): k=d.get("key","?"); return "Created: ["+k+"]("+JIRA_URL+"/browse/"+k+")"
                return "Jira error "+str(r.status)+": "+str(d.get("errorMessages",d))
    except Exception as e: return "Jira: "+str(e)

async def jira_list():
    if not JIRA_EMAIL or not JIRA_TOKEN_J: return "Jira not configured"
    auth=base64.b64encode((JIRA_EMAIL+":"+JIRA_TOKEN_J).encode()).decode()
    hdr={"Authorization":"Basic "+auth,"Accept":"application/json"}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(JIRA_URL+"/rest/api/3/search?jql=project="+JIRA_PROJECT+"+ORDER+BY+created+DESC&maxResults=10",headers=hdr,timeout=aiohttp.ClientTimeout(total=15)) as r:
                d=await r.json()
                if r.status==200:
                    issues=d.get("issues",[])
                    if not issues: return "No tasks"
                    return chr(10).join("- ["+i["key"]+"] "+i["fields"]["summary"][:60]+" " for i in issues)
                return "Jira error "+str(r.status)
    except Exception as e: return "Jira: "+str(e)

def run_cmd(cmd):
    if any(b in cmd for b in ["rm -rf /","mkfs","shutdown","reboot","halt"]): return "BLOCKED"
    try:
        r=subprocess.run(cmd,shell=True,capture_output=True,text=True,timeout=30)
        return(r.stdout or r.stderr or "OK")[:3000]
    except subprocess.TimeoutExpired: return "Timeout"
    except Exception as e: return str(e)

def get_status():
    try:
        mem_raw=subprocess.check_output(["free","-h"],text=True)
        disk_raw=subprocess.check_output(["df","-h","/"],text=True)
        up=subprocess.check_output(["uptime","-p"],text=True).strip()
        dc=subprocess.check_output(["docker","ps","--format","{{.Names}}: {{.Status}}"],text=True).strip()
        mem_line=[l for l in mem_raw.split(chr(10)) if "Mem:" in l][0].split()
        disk_line=disk_raw.strip().split(chr(10))[-1].split()
        return "RAM: "+mem_line[2]+"/"+mem_line[1]+chr(10)+"Disk: "+disk_line[2]+"/"+disk_line[1]+" ("+disk_line[4]+")"+chr(10)+"Uptime: "+up+chr(10)+"Docker:"+chr(10)+dc
    except Exception as e: return str(e)
async def heartbeat_loop():
    await asyncio.sleep(300)
    while True:
        try:
            if OWNER_ID:
                pool=await get_db()
                async with pool.acquire() as c:
                    task=await c.fetchrow("SELECT * FROM tasks_queue WHERE status=$1 ORDER BY priority DESC LIMIT 1","pending")
                    if task:
                        tid=task["id"];ttitle=task["title"];tdesc=task["description"] or ""
                        await c.execute("UPDATE tasks_queue SET status=$1,updated_at=NOW() WHERE id=$2","in_progress",tid)
                        await bot.send_message(OWNER_ID,"HB: "+ttitle,parse_mode=ParseMode.MARKDOWN)
                        res,model=await ask_ai([{"role":"user","content":"Task: "+ttitle+chr(10)+tdesc+chr(10)+"Report."}],True)
                        await c.execute("UPDATE tasks_queue SET status=$1,result=$2,updated_at=NOW() WHERE id=$3","done",res,tid)
                        await log_action("heartbeat","done "+str(tid),res[:200])
                        await bot.send_message(OWNER_ID,"Done: "+ttitle+chr(10)+chr(10)+res[:800],parse_mode=ParseMode.MARKDOWN)
        except Exception as e: log.error(f"HB: {e}")
        await asyncio.sleep(1800)

@dp.message(CommandStart())
async def cmd_start(msg:types.Message):
    global OWNER_ID; OWNER_ID=msg.from_user.id
    models=await get_local_models()
    ls="LOCAL: "+", ".join(models) if models else "LOCAL: offline"
    n=chr(10)
    text="*GETAI v3* Online"+n+"_"+ls+"_"+n+n
    text+="`/status` - server"+n+"`/docker` - containers"+n+"`/sh <cmd>` - shell"+n
    text+="`/task <desc>` - add task"+n+"`/tasks` - list"+n+"`/agent <task>` - agents"+n
    text+="`/jira [list|task]` - Jira"+n+"`/memory` - stats"+n+"`/models` - models"+n+n
    text+="Or just write - local AI responds"
    await msg.answer(text,parse_mode=ParseMode.MARKDOWN)
    await save_msg(msg.from_user.id,"assistant","v3")

@dp.message(Command("models"))
async def cmd_models(msg:types.Message):
    models=await get_local_models(); n=chr(10)
    text="*Local Ollama (0 tokens):*"+n+"".join("- `"+m+"`"+n for m in models)
    text+=n+"*Groq fallback:* "+GROQ_FAST+", "+GROQ_SMART
    await msg.answer(text,parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("status"))
async def cmd_status(msg:types.Message):
    n=chr(10)
    await msg.answer("*Server:*"+n+"```"+n+get_status()+n+"```",parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("docker"))
async def cmd_docker(msg:types.Message):
    n=chr(10)
    await msg.answer("*Docker:*"+n+"```"+n+run_cmd("docker ps -a")+n+"```",parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("sh"))
async def cmd_sh(msg:types.Message):
    cmd=msg.text.replace("/sh","",1).strip(); n=chr(10)
    if not cmd: await msg.answer("Example: `/sh ls -la`",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("`"+cmd+"`",parse_mode=ParseMode.MARKDOWN)
    await msg.answer("```"+n+run_cmd(cmd)+n+"```",parse_mode=ParseMode.MARKDOWN)
    await log_action("devops","shell:"+cmd,"ok")

@dp.message(Command("logs"))
async def cmd_logs(msg:types.Message):
    args=msg.text.split(); c=args[1] if len(args)>1 else "ai-company-bot-1"
    n=chr(10)
    await msg.answer("```"+n+run_cmd("docker logs --tail 30 "+c)+n+"```",parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("task"))
async def cmd_task(msg:types.Message):
    desc=msg.text.replace("/task","",1).strip()
    if not desc: await msg.answer("Example: `/task description`",parse_mode=ParseMode.MARKDOWN); return
    pool=await get_db()
    async with pool.acquire() as c: tid=await c.fetchval("INSERT INTO tasks_queue (title,description,priority) VALUES ($1,$2,5) RETURNING id",desc[:200],desc)
    await msg.answer("Task #"+str(tid)+" added",parse_mode=ParseMode.MARKDOWN)
    await log_action("manager","task #"+str(tid),desc[:80])

@dp.message(Command("tasks"))
async def cmd_tasks(msg:types.Message):
    pool=await get_db(); n=chr(10)
    async with pool.acquire() as c: rows=await c.fetch("SELECT id,title,status FROM tasks_queue ORDER BY priority DESC LIMIT 10")
    if not rows: await msg.answer("Queue empty"); return
    icons={"pending":"[wait]","in_progress":"[run]","done":"[done]","failed":"[fail]"}
    text="*Tasks:*"+n+"".join(icons.get(r["status"],"?")+
" #"+str(r["id"])+" `"+r["title"][:50]+"`"+n for r in rows)
    await msg.answer(text,parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("agent"))
async def cmd_agent(msg:types.Message):
    task=msg.text.replace("/agent","",1).strip(); n=chr(10)
    if not task: await msg.answer("Example: `/agent create REST API`",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("Running agents... ~30s",parse_mode=ParseMode.MARKDOWN)
    result=await orchestrate(task)
    await msg.answer(result[:4000],parse_mode=ParseMode.MARKDOWN)
    pool=await get_db()
    async with pool.acquire() as c: await c.execute("INSERT INTO tasks_queue (title,description,status,result) VALUES ($1,$2,$3,$4)",task[:200],task,"done",result[:2000])

@dp.message(Command("jira"))
async def cmd_jira(msg:types.Message):
    parts=msg.text.split(None,1); sub=parts[1].strip() if len(parts)>1 else "list"
    if sub.lower()=="list": await msg.answer("*Jira:*"+chr(10)+await jira_list(),parse_mode=ParseMode.MARKDOWN)
    else:
        res=await jira_create(sub[:255],sub)
        await msg.answer(res,parse_mode=ParseMode.MARKDOWN)
        await log_action("manager","jira:"+sub[:60],res)

@dp.message(Command("memory"))
async def cmd_memory(msg:types.Message):
    pool=await get_db(); n=chr(10)
    async with pool.acquire() as c:
        m=await c.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id=$1",msg.from_user.id)
        t=await c.fetchval("SELECT COUNT(*) FROM tasks_queue")
        l=await c.fetchval("SELECT COUNT(*) FROM agent_logs")
    await msg.answer("*Memory:*"+n+"Messages: "+str(m)+n+"Tasks: "+str(t)+n+"Logs: "+str(l),parse_mode=ParseMode.MARKDOWN)

@dp.message()
async def handle_all(msg:types.Message):
    global OWNER_ID; OWNER_ID=OWNER_ID or msg.from_user.id
    text=msg.text or ""
    await save_msg(msg.from_user.id,"user",text)
    try:
        ans,model=await smart_answer(msg.from_user.id,text)
        await msg.answer(ans or "No response",parse_mode=ParseMode.MARKDOWN)
        await save_msg(msg.from_user.id,"assistant",ans,model)
        await maybe_summarize(msg.from_user.id)
        log.info("Model: "+model)
    except Exception as e:
        log.error("Handle: "+str(e))
        try: await msg.answer(str(e)[:500])
        except: pass

async def main():
    log.info("GETAI v3 - Ollama local + Groq fallback")
    asyncio.create_task(heartbeat_loop())
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
