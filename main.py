import asyncio,logging,os,subprocess,json,aiohttp,asyncpg,base64
from aiogram import Bot,Dispatcher,types
from aiogram.filters import CommandStart,Command
from aiogram.enums import ParseMode
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
log=logging.getLogger(__name__)

# Config
BOT_TOKEN     =os.getenv("TG_BOT_TOKEN","")
GROQ_KEY      =os.getenv("GROQ_API_KEY","")
OPENROUTER_KEY=os.getenv("OPENROUTER_API_KEY","")
GEMINI_KEY    =os.getenv("GEMINI_API_KEY","")
CEREBRAS_KEY  =os.getenv("CEREBRAS_API_KEY","")
TAVILY_KEY    =os.getenv("TAVILY_API_KEY","")
OLLAMA_BASE   =os.getenv("OLLAMA_HOST","http://172.17.0.1:11434")
DB_USER =os.getenv("POSTGRES_USER","ai_admin")
DB_PASS =os.getenv("POSTGRES_PASSWORD","secure_ai_pass_2026")
DB_NAME =os.getenv("POSTGRES_DB","ai_memory")
DB_HOST =os.getenv("DB_HOST","db")
JIRA_URL     =os.getenv("JIRA_URL","https://asranovmaksat.atlassian.net")
JIRA_EMAIL   =os.getenv("JIRA_EMAIL","")
JIRA_TOKEN_J =os.getenv("JIRA_TOKEN","")
JIRA_PROJECT =os.getenv("JIRA_PROJECT","GAI")
GITHUB_TOKEN =os.getenv("GITHUB_TOKEN","")
GITHUB_USER  =os.getenv("GITHUB_USER","Asranov-Get")

# Clients
bot=Bot(token=BOT_TOKEN)
dp=Dispatcher()
local_ai =AsyncOpenAI(api_key="ollama",  base_url=OLLAMA_BASE+"/v1")
groq_ai  =AsyncOpenAI(api_key=GROQ_KEY,  base_url="https://api.groq.com/openai/v1")

db_pool =None
OWNER_ID=None

# Models
LOCAL_CODER="qwen2.5:3b"  # 7b needs 8GB RAM, use 3b until upgrade
LOCAL_MAIN ="qwen2.5:3b"
LOCAL_FAST ="llama3.2:1b"
GROQ_FAST  ="llama-3.1-8b-instant"
GROQ_SMART ="llama-3.3-70b-versatile"

# Free daily limits
FREE_LIMITS={"groq":14400,"openrouter":1000,"gemini":1500,"cerebras":1000}

# Keywords
SIMPLE_KW =["привет","как дела","спасибо","ок","да","нет","hi","hello"]
COMPLEX_KW=["архитект","разработ","задач","проект","план","систем","код","база","напиши","создай"]
WEB_KW    =["найди","поищи","загугли","что такое","кто такой","новости","актуальн",
            "сейчас","2025","2026","курс","цена","погода","как сделать"]

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

async def get_db():
    global db_pool
    if db_pool is None:
        db_pool=await asyncpg.create_pool(host=DB_HOST,user=DB_USER,password=DB_PASS,database=DB_NAME,min_size=1,max_size=5)
    return db_pool

async def save_msg(uid,role,content,model=None):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            await c.execute("INSERT INTO conversations(user_id,role,content,model_used) VALUES(,,,)",uid,role,str(content)[:4000],model)
    except Exception as e: log.error(f"DB save:{e}")

async def get_history(uid,limit=8):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            s=await c.fetchval("SELECT summary FROM conversation_summaries WHERE user_id= ORDER BY created_at DESC LIMIT 1",uid)
            rows=await c.fetch("SELECT role,content FROM conversations WHERE user_id= ORDER BY created_at DESC LIMIT ",uid,limit)
        msgs=[{"role":r["role"],"content":r["content"]} for r in reversed(rows)]
        if s: msgs.insert(0,{"role":"system","content":"Context: "+s})
        return msgs
    except Exception as e: log.error(f"Hist:{e}"); return []

async def maybe_summarize(uid):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            cnt=await c.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id=",uid)
            if cnt>0 and cnt%20==0:
                rows=await c.fetch("SELECT role,content FROM conversations WHERE user_id= ORDER BY created_at DESC LIMIT 20",uid)
                hist=chr(10).join([r["role"]+": "+r["content"][:150] for r in reversed(rows)])
                s,_=await ask_ai([{"role":"user","content":"Compress to 3 sentences: "+hist}],True,True)
                if s: await c.execute("INSERT INTO conversation_summaries(user_id,summary,message_count) VALUES(,,)",uid,s,cnt)
    except Exception as e: log.error(f"Sum:{e}")

async def log_action(agent,action,details,status="ok"):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            await c.execute("INSERT INTO agent_logs(agent,action,details,status) VALUES(,,,)",agent,action[:200],str(details)[:1000],status)
    except Exception as e: log.error(f"Log:{e}")

async def get_usage(provider):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            v=await c.fetchval("SELECT COALESCE(SUM(count),0) FROM token_usage WHERE provider= AND date=CURRENT_DATE",provider)
            return int(v or 0)
    except: return 0

async def inc_usage(provider,model):
    try:
        pool=await get_db()
        async with pool.acquire() as c:
            await c.execute("INSERT INTO token_usage(provider,model,count,date) VALUES(,,1,CURRENT_DATE) ON CONFLICT(provider,model,date) DO UPDATE SET count=token_usage.count+1",provider,model)
    except: pass

async def ask_ai(messages,prefer_local=True,fast=False,code_task=False):
    # 1. Local Ollama - 0 tokens always
    models=await get_local_models()
    lm=LOCAL_CODER if (code_task and LOCAL_CODER in models) else (LOCAL_MAIN if LOCAL_MAIN in models else (LOCAL_FAST if LOCAL_FAST in models else None))
    if lm:
        try:
            resp=await local_ai.chat.completions.create(model=lm,messages=messages,temperature=0.3,max_tokens=2048)
            ans=resp.choices[0].message.content
            if ans and len(ans.strip())>5: return ans,"local:"+lm
        except Exception as e: log.warning(f"Local {lm}:{e}")
    # 2. Groq free (14400/day)
    if await get_usage("groq")<FREE_LIMITS["groq"]:
        m=GROQ_FAST if fast else GROQ_SMART
        try:
            resp=await groq_ai.chat.completions.create(model=m,messages=messages,temperature=0.3,max_tokens=2048)
            await inc_usage("groq",m)
            return resp.choices[0].message.content,"groq:"+m
        except Exception as e: log.warning(f"Groq:{e}")
    # 3. Cerebras free (1000/day) - ultra fast
    if CEREBRAS_KEY and await get_usage("cerebras")<FREE_LIMITS["cerebras"]:
        try:
            cb=AsyncOpenAI(api_key=CEREBRAS_KEY,base_url="https://api.cerebras.ai/v1")
            resp=await cb.chat.completions.create(model="llama-3.3-70b",messages=messages,temperature=0.3,max_tokens=2048)
            await inc_usage("cerebras","llama-3.3-70b")
            return resp.choices[0].message.content,"cerebras:llama-3.3-70b"
        except Exception as e: log.warning(f"Cerebras:{e}")
    # 4. OpenRouter free (1000/day, 29 models)
    if OPENROUTER_KEY and len(OPENROUTER_KEY)>10 and await get_usage("openrouter")<FREE_LIMITS["openrouter"]:
        try:
            or_ai=AsyncOpenAI(api_key=OPENROUTER_KEY,base_url="https://openrouter.ai/api/v1")
            or_m="qwen/qwen3-coder:free" if code_task else "qwen/qwen3-next-80b-a3b-instruct:free"
            resp=await or_ai.chat.completions.create(model=or_m,messages=messages,temperature=0.3,max_tokens=2048)
            await inc_usage("openrouter",or_m)
            return resp.choices[0].message.content,"openrouter:"+or_m
        except Exception as e: log.warning(f"OpenRouter:{e}")
    # 5. Gemini free (1500/day)
    if GEMINI_KEY and len(GEMINI_KEY)>10 and await get_usage("gemini")<FREE_LIMITS["gemini"]:
        try:
            g=AsyncOpenAI(api_key=GEMINI_KEY,base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
            resp=await g.chat.completions.create(model="gemini-2.0-flash",messages=messages,temperature=0.3,max_tokens=2048)
            await inc_usage("gemini","gemini-2.0-flash")
            return resp.choices[0].message.content,"gemini:flash"
        except Exception as e: log.warning(f"Gemini:{e}")
    # 6. Ollama fallback even if weak answer
    if lm:
        try:
            resp=await local_ai.chat.completions.create(model=lm,messages=messages,temperature=0.7,max_tokens=512)
            return resp.choices[0].message.content,"local:fallback:"+lm
        except: pass
    return "Все лимиты исчерпаны на сегодня. Используй /usage для деталей.","exhausted"

SYS="Ты GETAI Assistant - AI-ядро IT-компании get.kg Бишкек. Управляешь сервером и командой. Отвечай кратко на русском. Используй markdown."

async def smart_answer(uid,text):
    level=classify(text); fast=level in("simple","medium"); t=text.lower()
    if not text.startswith("/") and any(k in t for k in WEB_KW):
        raw=await web_search(text,3)
        if raw:
            msgs=[{"role":"system","content":SYS+" Используй данные из интернета."},{"role":"user","content":text+chr(10)+"Интернет:"+chr(10)+raw[:1500]}]
            ans,model=await ask_ai(msgs,False,True)
            return ans,"web+"+model
    history=await get_history(uid,6 if fast else 8)
    msgs=[{"role":"system","content":SYS}]+history+[{"role":"user","content":text}]
    return await ask_ai(msgs,True,fast)

async def web_search(query,max_results=5):
    try:
        from duckduckgo_search import AsyncDDGS
        results=[]
        async with AsyncDDGS() as ddgs:
            async for r in ddgs.text(query,max_results=max_results):
                results.append(r.get("title","")+chr(10)+r.get("href","")+chr(10)+r.get("body","")[:300])
        if results: return (chr(10)+"---"+chr(10)).join(results)
    except Exception as e: log.warning(f"DDG:{e}")
    if TAVILY_KEY:
        try:
            from tavily import TavilyClient
            resp=TavilyClient(TAVILY_KEY).search(query=query,max_results=max_results,search_depth="basic")
            results=[r.get("title","")+chr(10)+r.get("url","")+chr(10)+r.get("content","")[:300] for r in resp.get("results",[])]
            if results: return (chr(10)+"---"+chr(10)).join(results)
        except Exception as e: log.warning(f"Tavily:{e}")
    return ""

async def web_fetch(url):
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=aiohttp.ClientTimeout(total=10)) as r:
                html=await r.text(errors="ignore")
        from bs4 import BeautifulSoup
        soup=BeautifulSoup(html,"html.parser")
        for tag in soup(["script","style","nav","footer","header"]): tag.decompose()
        return " ".join(soup.get_text(separator=" ",strip=True).split())[:4000]
    except Exception as e: return "Error: "+str(e)

def run_cmd(cmd):
    if any(b in cmd for b in ["rm -rf /","mkfs","shutdown","reboot","halt"]): return "BLOCKED"
    try:
        r=subprocess.run(cmd,shell=True,capture_output=True,text=True,timeout=60)
        return (r.stdout or r.stderr or "OK")[:3000]
    except subprocess.TimeoutExpired: return "Timeout 60s"
    except Exception as e: return str(e)

def get_status():
    try:
        mem_lines=subprocess.check_output(["free","-h"],text=True).split(chr(10))
        mem=[l for l in mem_lines if "Mem:" in l][0].split()
        disk_lines=subprocess.check_output(["df","-h","/"],text=True).split(chr(10))
        disk=disk_lines[-2].split() if len(disk_lines)>1 else ["?","?","?","?","?"]
        up=subprocess.check_output(["uptime","-p"],text=True).strip()
        dc=subprocess.check_output(["sh","-c","docker ps --format {{.Names}}: {{.Status}}"],text=True).strip()
        ol=subprocess.check_output(["ollama","list"],text=True).strip()
        return "RAM: "+mem[2]+"/"+mem[1]+chr(10)+"Disk: "+disk[2]+"/"+disk[1]+chr(10)+"Up: "+up+chr(10)+"Docker:"+chr(10)+dc+chr(10)+"Ollama:"+chr(10)+ol
    except Exception as e: return str(e)

AGENT_SYS={
    "orchestrator":"Orchestrator GETAI: choose agent (manager/architect/backend/frontend/devops/qa). JSON only: {agent: name}",
    "manager":"Project Manager GETAI. Write TZ: goal, requirements, acceptance criteria. Russian markdown.",
    "architect":"Software Architect GETAI. Design: stack, architecture, risks. Markdown.",
    "backend":"Backend Developer GETAI. Python/FastAPI. Working code with comments.",
    "frontend":"Frontend Developer GETAI. React/Next.js/Tailwind. Adaptive components.",
    "devops":"DevOps GETAI. Ubuntu/Docker/Nginx. Bash, docker-compose. Safe.",
    "qa":"QA Engineer GETAI. Find bugs, write pytest. Specific comments.",
}

async def run_agent(name,task):
    msgs=[{"role":"system","content":AGENT_SYS.get(name,AGENT_SYS["manager"])},{"role":"user","content":task}]
    res,model=await ask_ai(msgs,True,False,name in("backend","frontend","devops"))
    await log_action(name,task[:80],res[:200])
    return res

async def orchestrate(task):
    raw,_=await ask_ai([{"role":"system","content":AGENT_SYS["orchestrator"]},{"role":"user","content":task}],True,True)
    try:
        s=raw.find("{"); agent=json.loads(raw[s:raw.find("}")+1]).get("agent","manager") if s>=0 else "manager"
    except: agent="manager"
    tz=await run_agent("manager",task)
    result=await run_agent(agent,task)
    qa=await run_agent("qa","Review: "+result[:400])
    n=chr(10)
    return "*Agent:* "+n+n+"*TZ:*"+n+tz[:400]+n+n+"*Result:*"+n+result[:500]+n+n+"*QA:*"+n+qa[:250]

# EXECUTOR - writes files, git push, docker restart
async def executor_write(filepath,content,desc=""):
    try:
        os.makedirs("/".join(filepath.split("/")[:-1]),exist_ok=True) if "/" in filepath else None
        open(filepath,"w",encoding="utf-8").write(content)
        await log_action("executor","write:"+filepath,desc or content[:80])
        return True,"Written: "+filepath
    except Exception as e: return False,"Error: "+str(e)

async def executor_git(repo,commit,branch="main"):
    r1=run_cmd("cd "+repo+" && git add -A")
    r2=run_cmd("cd "+repo+" && git commit -m +commit[:70]+ --allow-empty")
    r3=run_cmd("cd "+repo+" && git push origin "+branch+" 2>&1")
    await log_action("executor","git:"+repo,commit)
    return chr(10).join([r1[:100],r2[:100],r3[:150]])

async def plan_and_execute(task,uid):
    n=chr(10)
    plan_prompt=(
        "You are GETAI DevOps. Return JSON array of steps to execute this task."+n+
        "Step types: shell(cmd,desc), write_file(file,content,desc), git_push(repo,commit), npm_build(path), docker_restart(service)."+n+
        "Return ONLY JSON array. Task: "+task
    )
    raw,model=await ask_ai(
        [{"role":"system","content":"Return only valid JSON array of execution steps."},{"role":"user","content":plan_prompt}],
        True,False,True
    )
    try:
        s=raw.find("["); e2=raw.rfind("]")+1
        steps=json.loads(raw[s:e2]) if s>=0 and e2>s else []
    except: steps=[{"type":"shell","cmd":"echo Task: "+task[:50],"desc":"echo"}]

    results=[]
    for i,step in enumerate(steps[:10]):
        stype=step.get("type","shell"); sdesc=step.get("desc",stype)
        if OWNER_ID:
            try: await bot.send_message(OWNER_ID,"Step "+str(i+1)+"/"+str(len(steps))+": ",parse_mode=ParseMode.MARKDOWN)
            except: pass
        if stype=="shell":
            out=run_cmd(step.get("cmd","echo ok"))
            results.append("Step "+str(i+1)+" OK: "+out[:200])
        elif stype=="write_file":
            ok,out=await executor_write(step.get("file",""),step.get("content",""),sdesc)
            results.append("Step "+str(i+1)+": "+out)
        elif stype=="git_push":
            out=await executor_git(step.get("repo","/opt/getai-website"),step.get("commit",task[:70]))
            results.append("Step "+str(i+1)+" git: "+out[:200])
        elif stype=="npm_build":
            out=run_cmd("cd "+step.get("path","/opt/getai-website")+" && npm run build 2>&1 | tail -5")
            results.append("Step "+str(i+1)+" build: "+out[:200])
        elif stype=="docker_restart":
            out=run_cmd("cd /opt/ai-company && docker compose restart "+step.get("service","bot"))
            results.append("Step "+str(i+1)+" restart: "+out[:150])
    return chr(10).join(results),model,steps

# JIRA
async def jira_create(title,desc):
    if not JIRA_EMAIL or not JIRA_TOKEN_J: return "Jira: add JIRA_EMAIL + JIRA_TOKEN to .env"
    auth=base64.b64encode((JIRA_EMAIL+":"+JIRA_TOKEN_J).encode()).decode()
    hdr={"Authorization":"Basic "+auth,"Content-Type":"application/json","Accept":"application/json"}
    body={"fields":{"project":{"key":JIRA_PROJECT},"summary":title[:255],"description":{"type":"doc","version":1,"content":[{"type":"paragraph","content":[{"type":"text","text":desc[:3000]}]}]},"issuetype":{"name":"Task"}}}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(JIRA_URL+"/rest/api/3/issue",json=body,headers=hdr,timeout=aiohttp.ClientTimeout(total=15)) as r:
                d=await r.json()
                if r.status in(200,201): k=d.get("key","?"); return "Jira: ["+k+"]("+JIRA_URL+"/browse/"+k+")"
                return "Jira error "+str(r.status)+": "+str(d.get("errorMessages",d))[:100]
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
                    if not issues: return "No tasks in Jira"
                    return chr(10).join("- ["+i["key"]+"] "+i["fields"]["summary"][:60]+" " for i in issues)
                return "Jira error "+str(r.status)
    except Exception as e: return "Jira: "+str(e)

# HEARTBEAT - autonomous task execution
async def heartbeat_loop():
    await asyncio.sleep(300)
    while True:
        try:
            if OWNER_ID:
                pool=await get_db()
                async with pool.acquire() as c:
                    task=await c.fetchrow("SELECT * FROM tasks_queue WHERE status= ORDER BY priority DESC LIMIT 1","pending")
                    if task:
                        tid=task["id"]; ttitle=task["title"]; tdesc=task["description"] or ""
                        await c.execute("UPDATE tasks_queue SET status=,updated_at=NOW() WHERE id=","in_progress",tid)
                        await bot.send_message(OWNER_ID,"Heartbeat: *"+ttitle+"*",parse_mode=ParseMode.MARKDOWN)
                        res,model=await ask_ai([{"role":"user","content":"Task: "+ttitle+chr(10)+tdesc+chr(10)+"Execute and report."}],True,False,True)
                        await c.execute("UPDATE tasks_queue SET status=,result=,updated_at=NOW() WHERE id=","done",res,tid)
                        await log_action("heartbeat","done #"+str(tid)+" via "+model,res[:200])
                        await bot.send_message(OWNER_ID,"Done #"+str(tid)+": *"+ttitle+"*"+chr(10)+chr(10)+res[:800]+chr(10)+"_"+model+"_",parse_mode=ParseMode.MARKDOWN)
        except Exception as e: log.error(f"HB:{e}")
        await asyncio.sleep(1800)

@dp.message(CommandStart())
async def cmd_start(msg:types.Message):
    global OWNER_ID; OWNER_ID=msg.from_user.id
    models=await get_local_models()
    ls="LOCAL: "+", ".join(models) if models else "LOCAL: offline"
    n=chr(10)
    await msg.answer("*GETAI v4* Online"+n+"_"+ls+"_"+n+n+"`/status` - server"+n+"`/docker` - containers"+n+"`/sh` - shell"+n+"`/search` - web"+n+"`/fetch` - page"+n+"`/task` - queue"+n+"`/tasks` - list"+n+"`/agent` - agents"+n+"`/plan` - autonomous"+n+"`/jira` - Jira"+n+"`/memory` - memory"+n+"`/usage` - limits"+n+"`/models` - models"+n+n+"Or write - local AI (0 tokens)",parse_mode=ParseMode.MARKDOWN)
    await save_msg(msg.from_user.id,"assistant","v4 started")

@dp.message(Command("models"))
async def cmd_models(msg:types.Message):
    models=await get_local_models(); n=chr(10)
    t="*Local (0 tokens):*"+n+"".join("- "+n for m in models)
    t+=n+"*Groq:* 14.4k/day "+n+"*Cerebras:* 1k/day"+n+"*OpenRouter:* 1k/day"+n+"*Gemini:* 1.5k/day"
    await msg.answer(t,parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("usage"))
async def cmd_usage(msg:types.Message):
    pool=await get_db(); n=chr(10)
    async with pool.acquire() as c:
        rows=await c.fetch("SELECT provider,SUM(count) as total FROM token_usage WHERE date=CURRENT_DATE GROUP BY provider ORDER BY total DESC")
    if not rows: await msg.answer("Today: 100% local!"); return
    lines=["*Usage today:*"+n]
    for r in rows:
        prov=r["provider"]; used=int(r["total"]); lim=FREE_LIMITS.get(prov,99999)
        pct=int(used/lim*100) if lim<99999 else 0
        lines.append(": "+str(used)+"/"+str(lim)+" "+str(pct)+"%")
    lines.append(n+"_Ollama: unlimited_")
    await msg.answer(chr(10).join(lines),parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("status"))
async def cmd_status(msg:types.Message):
    n=chr(10)
    await msg.answer("*Server:*"+n+"",parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("docker"))
async def cmd_docker(msg:types.Message):
    n=chr(10)
    await msg.answer("*Docker:*"+n+"",parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("sh"))
async def cmd_sh(msg:types.Message):
    cmd=msg.text.replace("/sh","",1).strip(); n=chr(10)
    if not cmd: await msg.answer("Example: ",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("",parse_mode=ParseMode.MARKDOWN)
    await msg.answer("",parse_mode=ParseMode.MARKDOWN)
    await log_action("devops","shell:"+cmd,"ok")

@dp.message(Command("logs"))
async def cmd_logs(msg:types.Message):
    args=msg.text.split(); c=args[1] if len(args)>1 else "ai-company-bot-1"; n=chr(10)
    await msg.answer("",parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("search"))
async def cmd_search(msg:types.Message):
    query=msg.text.replace("/search","",1).strip()
    if not query: await msg.answer("Example: ",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("Searching...",parse_mode=ParseMode.MARKDOWN)
    raw=await web_search(query,4)
    if not raw: await msg.answer("No results."); return
    ans,model=await ask_ai([{"role":"system","content":"Answer briefly in Russian. Markdown."},{"role":"user","content":"Q: "+query+chr(10)+"Results:"+chr(10)+raw[:2000]}],True,True)
    await msg.answer(ans[:4000]+chr(10)+"_"+model+"_",parse_mode=ParseMode.MARKDOWN)
    await log_action("web","search:"+query[:60],ans[:200])

@dp.message(Command("fetch"))
async def cmd_fetch(msg:types.Message):
    url=msg.text.replace("/fetch","",1).strip()
    if not url.startswith("http"): await msg.answer("Example: ",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("Loading...",parse_mode=ParseMode.MARKDOWN)
    text=await web_fetch(url)
    s,model=await ask_ai([{"role":"user","content":"5-sentence summary:"+chr(10)+text}],True,True)
    await msg.answer("*Summary:*"+chr(10)+s[:3000],parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("task"))
async def cmd_task(msg:types.Message):
    desc=msg.text.replace("/task","",1).strip()
    if not desc: await msg.answer("Example: ",parse_mode=ParseMode.MARKDOWN); return
    pool=await get_db()
    async with pool.acquire() as c: tid=await c.fetchval("INSERT INTO tasks_queue(title,description,priority) VALUES(,,5) RETURNING id",desc[:200],desc)
    await msg.answer("Task #"+str(tid)+" added",parse_mode=ParseMode.MARKDOWN)
    await log_action("manager","task #"+str(tid),desc[:80])

@dp.message(Command("tasks"))
async def cmd_tasks(msg:types.Message):
    pool=await get_db(); n=chr(10)
    async with pool.acquire() as c: rows=await c.fetch("SELECT id,title,status FROM tasks_queue ORDER BY priority DESC LIMIT 10")
    if not rows: await msg.answer("Queue empty"); return
    icons={"pending":"Wait","in_progress":"Run","done":"Done","failed":"Fail"}
    t="*Tasks:*"+n+"".join("["+icons.get(r["status"],"?")+"] #"+str(r["id"])+" "+n for r in rows)
    await msg.answer(t,parse_mode=ParseMode.MARKDOWN)

@dp.message(Command("agent"))
async def cmd_agent(msg:types.Message):
    task=msg.text.replace("/agent","",1).strip()
    if not task: await msg.answer("Example: ",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("Running agents...",parse_mode=ParseMode.MARKDOWN)
    result=await orchestrate(task)
    await msg.answer(result[:4000],parse_mode=ParseMode.MARKDOWN)
    pool=await get_db()
    async with pool.acquire() as c: await c.execute("INSERT INTO tasks_queue(title,description,status,result) VALUES(,,,)",task[:200],task,"done",result[:2000])

@dp.message(Command("plan"))
async def cmd_plan(msg:types.Message):
    task=msg.text.replace("/plan","",1).strip(); n=chr(10)
    if not task: await msg.answer("*Autonomous:*"+n+""+n+"",parse_mode=ParseMode.MARKDOWN); return
    await msg.answer("Planning and executing...",parse_mode=ParseMode.MARKDOWN)
    result,model,steps=await plan_and_execute(task,msg.from_user.id)
    pool=await get_db()
    async with pool.acquire() as c: tid=await c.fetchval("INSERT INTO tasks_queue(title,description,status,result) VALUES(,,,) RETURNING id",task[:200],task,"done",result[:2000])
    await msg.answer("Done! Task #"+str(tid)+chr(10)+"Model: "+chr(10)+"Steps: "+str(len(steps))+chr(10)+chr(10)+"",parse_mode=ParseMode.MARKDOWN)

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
        m=await c.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id=",msg.from_user.id)
        t=await c.fetchval("SELECT COUNT(*) FROM tasks_queue")
        l=await c.fetchval("SELECT COUNT(*) FROM agent_logs")
    await msg.answer("*Memory:*"+n+"Msgs: "+str(m)+n+"Tasks: "+str(t)+n+"Logs: "+str(l),parse_mode=ParseMode.MARKDOWN)

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
    log.info("GETAI v4 - Ollama+Groq+Cerebras+OpenRouter+Gemini cascade")
    asyncio.create_task(heartbeat_loop())
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
