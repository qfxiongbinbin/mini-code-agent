# Mini Code Agent v0

这是一个教学用的最小代码代理。

它现在支持通过 OpenAI 兼容接口接入国内模型：

- 智谱 `zhipu`
- MiniMax `minimax`

## 目录

- `main.py`：主程序
- `requirements.txt`：依赖
- `.env`：本地配置

## 准备

```bash
cd "/Users/xiongbin/codespace/mini-code-agent-v0"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 配置

`.env` 推荐这样写：

```env
PROVIDER=zhipu
API_KEY=你的key
MODEL=glm-5
BASE_URL=https://open.bigmodel.cn/api/paas/v4/
AGENT_WORKSPACE=/Users/xiongbin/codespace/mini-code-agent-v0
MAX_STEPS=8
THINKING_TYPE=enabled
```

如果切到 MiniMax（中国区默认端点）：

```env
PROVIDER=minimax
API_KEY=你的key
MODEL=MiniMax-M2.5
BASE_URL=https://api.minimaxi.com/v1

AGENT_WORKSPACE=/Users/xiongbin/codespace/mini-code-agent-v0
MAX_STEPS=8
THINKING_TYPE=disabled

```

## 运行

项目已经内置 `python-dotenv`，直接运行即可：

```bash
python3 main.py "帮我搜索项目里和 login 相关的代码，并说明调用链"
```

或者：

```bash
python3 main.py "创建一个 hello.py，内容是打印 hello agent，然后运行它"
```

## 你应该重点看什么

1. `TOOL_SCHEMAS`
2. `Agent.run()`
3. `LocalTools.call()`
4. `PROVIDER / BASE_URL / MODEL` 这组抽象

## 当前限制

- `run_command` 只允许少量安全命令
- 只做最小错误处理
- 兼容模式基于 `chat.completions`
- 没做 diff、测试重试、上下文压缩

这是刻意的：先把核心闭环学会，再继续加复杂度。
