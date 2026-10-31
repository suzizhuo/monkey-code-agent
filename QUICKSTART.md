# Isolis 快速上手（大白话版）

这份文档不讲原理，只讲怎么用。照着敲就行。

---

## 一、先说清楚这是个啥

Isolis 是一个"养 Agent 的笼子"。

你在终端里敲命令，它会开一个 Docker 容器，在容器里跑一个 AI 编程助手（Agent）。你跟这个 Agent 聊天，它能帮你写代码、跑命令、装软件、开浏览器看网页。

关键点：Agent 住在容器里，碰不到你的电脑，也碰不到别的 Agent。你随时可以把它删掉重来，电脑还是干净的。

---

## 二、第一次用，只要三步

打开终端，进到项目目录：

```bash
cd /workspace
```

**第一步：体检**

```bash
python3 isolis.py doctor
```

看到最后一行是 `result: ready` 就说明环境没问题，可以往下走。如果出现 `[fail]`，先按提示修，别硬上。

**第二步：初始化**

```bash
python3 isolis.py init
```

第一次会下载和构建镜像，比较慢（十几分钟到半小时都正常），看到：

```
agent 'default' ready on 127.0.0.1:18080
```

就成功了。以后再用就很快，因为镜像已经建好了。

**第三步：聊天**

```bash
python3 isolis.py chat
```

看到 `you>` 就可以打字了。想退出就输入 `/quit`。

三步完事。

---

## 三、常用命令速查表

都是 `python3 isolis.py` 后面接一个词。

| 命令 | 干啥用的 | 什么时候用 |
|---|---|---|
| `doctor` | 检查环境有没有问题 | 第一次用，或者报错的时候 |
| `setup` | 准备好 Docker、镜像、网络 | 换电脑后第一次用 |
| `init` | 建一个 Agent 并启动 | 第一次用，或者想开个新的 |
| `chat` | 跟 Agent 聊天 | 干活的时候 |
| `status` | 看看现在有哪些 Agent | 忘了自己开了几个的时候 |
| `use 名字` | 切换到某个 Agent | 有多个 Agent 的时候 |
| `stop 名字` | 关掉某个 Agent | 不用了，省内存 |
| `rebuild` | 重做镜像并重建 Agent | 改了 Agent 代码之后 |
| `profiles` | 看看有哪几种权限档位 | 想知道 Agent 能干啥的时候 |
| `help` | 看帮助 | 忘了命令的时候 |

也可以不带参数直接进交互模式：

```bash
python3 isolis.py
```

然后会出现 `isolis>` 提示符，上面这些命令直接敲，不用再打 `python3 isolis.py`。

---

## 四、怎么进入 Agent 聊天

**方式一：一条命令直接进**

```bash
python3 isolis.py chat
```

前提是你已经 `init` 过。如果还没建，它会自动帮你建一个。

进去之后会先打印一行状态，长这样：

```
chat: default | profile tooled | hostname 3bb72a65dae8 | model xxx | llm_host xxx | via mc-direct
tools: run_command, write_file, read_file, list_files, browse, install_packages, install_android_sdk
```

第二行是这次 Agent 手里有哪些工具，心里有个数。

然后就是 `you>` 提示符，直接说人话：

```
you> 帮我写一个 Python 脚本，把当前目录的文件按大小排序
```

**聊天里的几个指令：**

| 输入 | 干啥 |
|---|---|
| 普通文字 | 发给 Agent |
| `/back` | 退出聊天，回到 `isolis>` |
| `/quit` | 直接退出整个程序 |
| `/help` | 看这几个指令 |

**方式二：先进交互模式再聊**

```bash
python3 isolis.py
```

```bash
isolis> init
isolis> chat
```

效果一样，适合要连着干好几件事的时候。

**小提示：** 每次 `chat` 都会连到同一个 Agent，它记得之前聊过什么。想清空记忆就 `stop` 掉再 `init`。

**几个默认上限（知道一下就行）：**

| 限制 | 数值 | 意思 |
|---|---|---|
| 记忆轮数 | 400 轮 | 再早的对话会被丢掉，不用手动清 |
| 单次工具步数 | 8 步 | 一次提问里模型最多连着调 8 次工具，防止死循环 |
| 单条命令超时 | 120 秒 | 跑太久的命令会被掐掉 |
| 装软件超时 | 30 分钟 | 下载大 SDK 也够用 |
| 单次返回长度 | 20000 字符 | 太长的结果会被截断，末尾有提示 |

这些是程序里写死的，够日常用。真不够用就让它把结果写进文件再读。

**聊久了会自动压缩上下文：**

Agent 记忆有两个阀门：

1. **按条数**：最多留 400 轮对话，更早的自动丢掉。
2. **按 token**：对话累积超过 90 万 token 时，它会把最旧的 40 条交给模型浓缩成一段摘要，用 `[compressed history]` 开头放在历史最前面，然后继续聊。

90 万 token 是个保守值。实测这个网关的硬上限是 100 万 token（超过就直接报错），所以程序会把你设的更大值自动压到 100 万，并提示一行 notice。

所以你可以一直聊下去，不会因为聊太长而崩。手机上不太直观，想看它压了多少：

```bash
curl -s http://127.0.0.1:18080/info | grep -o '"compressed_turns":[0-9]*'
```

**想关掉自动压缩：**

```bash
export ISOLIS_CONTEXT_TOKENS=0
```

这样只保留"400 轮"这一个阀门。

**想调小预算省成本**（按你实际需要）：

```bash
export ISOLIS_CONTEXT_TOKENS=200000
export ISOLIS_MAX_TURNS=100
```

**改完必须重建**：这些值在 `init` 时写进容器环境变量，改了要 `stop` 再 `init`，`rebuild` 不会更新已有容器。

**单条消息太长也会被截**：超过 90 万字符的一条消息，会保留头尾、中间截掉，并标注删了多少字。实测 60 万字符（约 17 万 token）能正常收发，想调小：

```bash
export ISOLIS_MAX_MESSAGE_CHARS=200000
```

**想看完整历史和归档**（被压缩或丢弃的原文都存在里面）：

```bash
curl -s http://127.0.0.1:18080/history | python3 -m json.tool | less
```

---

## 五、怎么初始化 Agent

最简单的：

```bash
python3 isolis.py init
```

这会建一个名字叫 `default` 的 Agent。

**想自己起名字：**

```bash
python3 isolis.py init myproject
```

名字会自动清洗，只保留字母、数字、下划线、短横线，最长 40 个字符。如果你输入的名字被改了，会有一行 `notice:` 告诉你改成了啥。

**想一次开好几个：**

```bash
python3 isolis.py init backend
python3 isolis.py init frontend
python3 isolis.py status
python3 isolis.py use backend
```

每个 Agent 是独立的容器，互不干扰，各占一个端口（18080 到 18119 之间）。

**注意：** 端口就 40 个，开太多会报错。用完记得 `stop`。

---

## 六、怎么查看项目源代码

这里说的"源代码"有两个，别搞混了。

### 1. 看 Isolis 自己的代码（跑在你电脑上的那部分）

就在项目目录里：

| 文件 | 里面是啥 |
|---|---|
| `isolis.py` | 主程序，管命令、开容器、分配端口 |
| `agent/server.py` | 容器里跑的那个 Agent，管聊天和工具 |
| `agent/Dockerfile` | 镜像怎么装的 |
| `agent/install-android-sdk.sh` | 装安卓 SDK 的脚本 |
| `agent/requirements.txt` | Python 依赖 |
| `ISOLIS_PROMPT.md` | 完整规格说明书（英文），想搞懂细节看这个 |

直接用编辑器打开看，或者：

```bash
less isolis.py
```

### 2. 看 Agent 干活时写的代码

Agent 的工作目录在容器里的 `/work`。它写的文件、克隆的仓库、跑出来的截图，全在 `/work` 里。

想拿出来看，进容器：

```bash
docker exec -it isolis-default ls -la /work
```

想把文件拷到电脑上：

```bash
docker cp isolis-default:/work/你的文件 ./本地文件
```

注意容器名 = `isolis-` 加 Agent 名字。

**重要：** `/work` 是临时内存盘，容器一删东西就没了。重要文件记得拷出来。

### 3. 让 Agent 自己给你看

直接在聊天里说：

```
you> 列一下 /work 里都有什么文件
you> 把 main.py 的内容读出来给我看
```

它会用 `list_files` 和 `read_file` 工具，把结果告诉你。

---

## 七、怎么让 Agent 装软件（比如安卓 SDK）

这里要理解一个概念：Agent 有两种"权限档位"。

| 档位 | 能不能装软件 | 适合啥场景 |
|---|---|---|
| `strict`（严格） | 不能 | 跑不信任的代码，最安全 |
| `tooled`（工具版） | 能 | 正常开发，默认就是它 |

默认是 `tooled`，所以一般不用管。

**装安卓 SDK，直接在聊天里说：**

```
you> 帮我装一下安卓 SDK
```

或者更明确一点：

```
you> 用 install_android_sdk 装默认的包
```

它会装好 `platform-tools`、`platforms;android-34`、`build-tools;34.0.0`，装完 `/opt/android-sdk/platform-tools/adb` 就能用了。

**装别的东西，也说人话：**

```
you> 用 apt 装一下 imagemagick
you> 用 pip 装一下 requests
you> 用 npm 装一下 typescript
```

Agent 会自己挑对应的工具。

**如果你就是想用严格模式：**

```bash
python3 isolis.py init safe --profile strict
python3 isolis.py use safe
python3 isolis.py chat
```

在严格模式下，你让它装东西，它会告诉你装不了。这是正常的，别再追问。

**注意：** 装的东西都在容器里。容器一删，装的全没了，下次得重装。这是为了不弄脏你的电脑。

---

## 八、怎么换模型

默认用平台注入的模型，不用你操心。想换的话有三种办法，优先级从高到低：

**办法一：命令行临时指定（只对这次有效）**

```bash
python3 isolis.py init --model 某个模型名
```

**办法二：设环境变量（这个终端里一直有效）**

```bash
export ISOLIS_MODEL=某个模型名
python3 isolis.py init
```

**办法三：什么都不设，用平台的默认值**

平台会给你 `MCAI_LLM_MODEL` 这个环境变量，程序自动用。

**要是模型名写了但不认识：** 程序会自己从平台能用的模型里挑一个，不会直接崩。想指定兜底模型可以设 `ISOLIS_DEFAULT_MODEL`。

---

## 九、怎么用别人的 Agent 仓库

默认用项目自带的 `agent/` 目录。也可以让它从 GitHub 拉别人的：

```bash
python3 isolis.py init myagent --source https://github.com/某人/某个仓库.git
```

程序会：

1. 把仓库下载到 `.isolis/agent-src/仓库名/`
2. 检查里面有没有 `Dockerfile`（没有就报错）
3. 给它单独建一个镜像
4. 启动容器

**再跑一次会怎样？** 会先 `git pull` 更新代码，然后发现镜像没变就直接复用，不会重复构建，很快。

**想回到本地代码：**

```bash
python3 isolis.py init myagent --source local
```

或者干脆不写 `--source`，默认就是 `local`。

**注意：** 别人的仓库里 `Dockerfile` 想干啥就干啥，用之前最好先看一眼，别随便跑来路不明的仓库。

---

## 十、几种常见操作

### 看看现在有几个 Agent

```bash
python3 isolis.py status
```

输出长这样：

```
* default              running  127.0.0.1:18080  tooled     xxx
  backend              running  127.0.0.1:18081  strict     xxx
```

`*` 是当前正在用的那个。下面还会告诉你当前 Agent 的隔离配置。

### 切换 Agent

```bash
python3 isolis.py use backend
```

切换之后 `chat` 连的就是 `backend` 了。

### 关掉 Agent

```bash
python3 isolis.py stop backend
```

不写名字就关当前那个。容器会被删掉，`/work` 里的东西也跟着没了，注意备份。

### 改了 Agent 代码，想让它生效

```bash
python3 isolis.py rebuild
```

会重新构建镜像，然后把当前 Agent 删掉重建。**里面的数据会丢。**

### 换电脑了，第一次用

```bash
python3 isolis.py setup
python3 isolis.py doctor
python3 isolis.py init
```

`setup` 会帮你把 Docker 弄好、配镜像加速、建网络。国内网络下 Docker Hub 拉不动，它会自动配一个国内镜像源。

---

## 十一、出问题了怎么办

**问题：`docker CLI not found`**

没装 Docker。先装 Docker，再 `python3 isolis.py setup`。

**问题：`MonkeyCode LLM was not injected`**

平台没把模型配置传进来。检查这两个环境变量在不在：

```bash
echo ${MCAI_LLM_BASE_URL:+有}
echo ${MCAI_LLM_API_KEY:+有}
```

都没输出说明确实没注入，这种情况找平台，不要自己填别的厂商的 Key。

**问题：`agent 'xxx' failed health check`**

容器起来了但 Agent 没活。看日志：

```bash
docker logs isolis-default
```

**问题：`no free port`**

40 个端口用完了。`status` 看看哪些不用了，`stop` 掉。

**问题：构建特别慢**

正常。第一次要下几百兆的东西。国内网络下 `setup` 会配镜像加速，别跳过这步。

**问题：装了安卓 SDK，但是 `adb` 找不到**

指明路径用：

```bash
/opt/android-sdk/platform-tools/adb version
```

**问题：完全搞不清状态了**

```bash
python3 isolis.py doctor
```

它会从头到尾检查一遍，哪里有问题一目了然。

---

## 十二、一句话总结每个命令

- `doctor` —— 先体检
- `setup` —— 搭环境
- `init` —— 开个 Agent
- `chat` —— 跟它说话
- `status` —— 看看有啥
- `use` —— 换个 Agent
- `stop` —— 关掉它
- `rebuild` —— 重做它
- `profiles` —— 看权限档位
- `help` —— 忘了就看这个

按这个顺序用，基本不会出错。
