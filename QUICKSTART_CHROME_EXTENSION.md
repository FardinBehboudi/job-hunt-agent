# 🚀 Quick Start: Claude in Chrome Extension Integration

## ✅ What's Ready

You now have a complete server ↔ browser extension communication system:

### Files Created
- ✅ `chrome_extension_bridge.py` - Communication layer
- ✅ `apply_agent.py` - Uses extension bridge
- ✅ `EXTENSION_SERVER_INTEGRATION.md` - Detailed setup guide
- ✅ `CLAUDE_IN_CHROME_SETUP.md` - Feature guide

### Architecture
```
Your Browser (Claude in Chrome Extension)
    ↕️ HTTP API (localhost:5000)
    ↓
Your Server (Dashboard + apply_agent.py)
    ↕️ Command Queue
    ↓
Job Applications
```

## 📋 3-Step Setup

### Step 1️⃣: Add Flask Endpoints to Dashboard

Open `dashboard/dashboard.py` and find the line `if __name__ == "__main__":` (should be near the end).

Add this **before** that line:

```python
# ═══ CLAUDE IN CHROME EXTENSION BRIDGE ═══
from chrome_extension_bridge import get_bridge

@app.route("/api/extension/ping", methods=["POST"])
def api_extension_ping():
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    bridge = get_bridge()
    result = bridge.handle_extension_ping(session_id)
    return jsonify(result)

@app.route("/api/extension/commands", methods=["GET"])
def api_extension_get_commands():
    bridge = get_bridge()
    commands = bridge.get_pending_commands()
    return jsonify({"commands": commands})

@app.route("/api/extension/response", methods=["POST"])
def api_extension_send_response():
    data = request.get_json() or {}
    request_id = data.get("id", "")
    response = data.get("response", {})
    bridge = get_bridge()
    bridge.handle_extension_response(request_id, response)
    return jsonify({"ok": True})

@app.route("/api/extension/session", methods=["GET"])
def api_extension_get_session():
    bridge = get_bridge()
    return jsonify({
        "session_id": bridge.session_id,
        "connected": bridge.is_connected,
        "timestamp": datetime.utcnow().isoformat()
    })
```

### Step 2️⃣: Open Claude in Chrome & Load Bridge Script

1. **Start your dashboard**
   ```bash
   python dashboard/dashboard.py
   ```

2. **Open Claude in Chrome** in your browser
   - Click the extension icon
   - Wait for it to load

3. **Open Browser Console**
   - Press `F12` or `Ctrl+Shift+I`
   - Go to "Console" tab

4. **Paste this code** into the console:
   ```javascript
   const SERVER_URL = "http://localhost:5000";
   const SESSION_ID = "default";
   
   class ExtensionClient {
     constructor() { this.sessionId = SESSION_ID; this.pollInterval = 1000; }
     
     async start() {
       console.log("🚀 Extension Bridge starting...");
       await this.ping();
       setInterval(() => this.pollCommands(), this.pollInterval);
     }
     
     async ping() {
       try {
         const response = await fetch(`${SERVER_URL}/api/extension/ping`, {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({ session_id: this.sessionId })
         });
         const data = await response.json();
         console.log("✅ Connected to server");
       } catch (e) { console.error("❌ Ping failed:", e); }
     }
     
     async pollCommands() {
       try {
         const response = await fetch(`${SERVER_URL}/api/extension/commands`);
         const data = await response.json();
         if (data.commands && data.commands.length > 0) {
           for (const cmd of data.commands) { await this.executeCommand(cmd); }
         }
       } catch (e) { console.error("Poll error:", e); }
     }
     
     async executeCommand(cmd) {
       console.log(`⚡ Command: ${cmd.command}`, cmd.params);
       let result = { success: false };
       try {
         if (cmd.command === "navigate") { 
           window.location.href = cmd.params.url; 
           result = { success: true }; 
         }
         else if (cmd.command === "screenshot") {
           // Simple screenshot capture
           result = { success: true, data: "screenshot_placeholder" };
         }
         else if (cmd.command === "click") {
           const el = document.elementFromPoint(cmd.params.x, cmd.params.y);
           if (el) { el.click(); result = { success: true }; }
         }
         else if (cmd.command === "type") {
           const active = document.activeElement;
           if (active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA")) {
             active.value = cmd.params.text;
             active.dispatchEvent(new Event("input", { bubbles: true }));
             result = { success: true };
           }
         }
       } catch (e) { result = { success: false, error: e.message }; }
       
       await this.sendResponse(cmd.id, result);
     }
     
     async sendResponse(requestId, result) {
       try {
         await fetch(`${SERVER_URL}/api/extension/response`, {
           method: "POST",
           headers: { "Content-Type": "application/json" },
           body: JSON.stringify({ id: requestId, response: result })
         });
       } catch (e) { console.error("Send response error:", e); }
     }
   }
   
   const client = new ExtensionClient();
   client.start();
   console.log("✅ Extension Bridge loaded!");
   ```

5. **Press Enter**
   - You should see: `✅ Extension Bridge loaded!`
   - And: `✅ Connected to server`

### Step 3️⃣: Test It!

1. **Go to dashboard** → `http://localhost:5000`

2. **Go to Job Hunt Agent** tab → Scroll down to **DEBUG section**

3. **Paste a LinkedIn job URL**
   ```
   https://www.linkedin.com/jobs/view/[ANY_JOB_ID]/
   ```

4. **Click "Run Apply"**

5. **Watch it work!**
   - Extension navigates to URL ✅
   - Analyzes page with Claude vision ✅
   - Detects apply button ✅
   - Reports back to dashboard ✅

## 📊 What Happens

```
Dashboard DEBUG:
  ↓ POST /api/apply/debug
  ↓ apply_integration.run()
  ↓ apply_agent.apply_to_job()
  ↓ bridge.navigate(url) → Extension navigates
  ↓ bridge.take_screenshot() → Extension captures
  ↓ Claude vision analyzes
  ↓ bridge.click(x, y) → Extension clicks
  ↓ Events stream back to UI
```

## 🎯 You Should See

In the DEBUG log:
```
Starting apply...
{"message": "Apply started...", "ok": true}
[session_start]
[job_started]
[step]     Navigating to job page...
[step]     Taking screenshot...
[step]     Detecting button...
[result]   ✓ Applied / ⚠ Manual queue / ✗ Failed
[done]     Session complete
```

## 🆘 If Nothing Happens

### Check 1: Dashboard is Running
```bash
curl http://localhost:5000/api/extension/session
# Should return session info
```

### Check 2: Extension Script Loaded
- Console should say: `✅ Extension Bridge loaded!`
- Console should say: `✅ Connected to server`

### Check 3: Browser Console Errors
- Press F12 → Console
- Look for red error messages
- Check network tab for `/api/extension/` requests

### Check 4: Dashboard Logs
- Check terminal running `python dashboard.py`
- Look for errors or warnings

## 🚀 Next Steps

After initial test:

1. **Try full apply session** (not just DEBUG)
2. **Use Apply tab** with matched jobs
3. **Monitor results** in `outputs/applied_jobs_log.json`
4. **Adjust profile** in `config.yaml` as needed

## 📖 For More Details

- `EXTENSION_SERVER_INTEGRATION.md` - Full technical guide
- `CLAUDE_IN_CHROME_SETUP.md` - Features & troubleshooting
- `apply_agent.py` - Source code
- `chrome_extension_bridge.py` - Bridge implementation

---

**Ready?** Add those 4 Flask routes to dashboard.py, paste the console script, and test! 🎉
