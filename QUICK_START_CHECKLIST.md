# ✅ Quick Start Checklist

## Pre-Flight Checks

- [ ] All Python files have valid syntax
  ```bash
  python test_integration.py
  ```
  Expected: `All integration tests passed!`

- [ ] Dashboard.py has extension bridge endpoints
  ```bash
  grep "/api/extension/ping" dashboard/dashboard.py
  ```
  Expected: `@app.route("/api/extension/ping", methods=["POST"])`

- [ ] ExtensionBridge.py exists and is complete
  ```bash
  python -c "from chrome_extension_bridge import get_bridge; print('✅ OK')"
  ```

- [ ] apply_agent.py uses ExtensionBridge
  ```bash
  grep "from chrome_extension_bridge import get_bridge" apply_agent.py
  ```

---

## Step 1: Start Dashboard (5 min)

```bash
cd C:\Users\f_beh\Projects\claude\job-hunt-agent
python dashboard/dashboard.py
```

Expected output:
```
 * Serving Flask app 'dashboard'
 * Debug mode: off
 * Running on http://127.0.0.1:5000
```

✅ **Check**: Open http://localhost:5000 - Dashboard loads


---

## Step 2: Open Claude in Chrome (2 min)

1. Launch Google Chrome
2. Navigate to any website (LinkedIn is good)
3. Click Claude in Chrome extension icon
4. Wait for Claude interface to load

✅ **Check**: Claude chat appears in side panel


---

## Step 3: Inject Extension Bridge (3 min)

1. In Claude window, press `F12` (Developer Tools)
2. Click **Console** tab
3. Copy-paste the code below
4. Press Enter

```javascript
const SERVER_URL = "http://localhost:5000";
const SESSION_ID = "default";

class ExtensionClient {
  constructor() {
    this.sessionId = SESSION_ID;
    this.pollInterval = 1000;
  }

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
      if (data.ok) {
        console.log("✅ Connected to server");
      }
    } catch (e) {
      console.error("❌ Ping failed:", e);
    }
  }

  async pollCommands() {
    try {
      const response = await fetch(`${SERVER_URL}/api/extension/commands`);
      const data = await response.json();
      
      if (data.commands && data.commands.length > 0) {
        for (const cmd of data.commands) {
          await this.executeCommand(cmd);
        }
      }
    } catch (e) {
      console.error("Poll error:", e);
    }
  }

  async executeCommand(cmd) {
    console.log(`⚡ Executing: ${cmd.command}`, cmd.params);
    
    let result = { success: false };
    
    try {
      switch (cmd.command) {
        case "navigate":
          window.location.href = cmd.params.url;
          result = { success: true };
          break;
          
        case "screenshot":
          result = await this.captureScreenshot();
          break;
          
        case "click":
          result = await this.clickElement(cmd.params.x, cmd.params.y);
          break;
          
        case "type":
          result = await this.typeText(cmd.params.text);
          break;
          
        default:
          result = { success: false, error: "Unknown command" };
      }
    } catch (e) {
      result = { success: false, error: e.message };
    }
    
    await this.sendResponse(cmd.id, result);
  }

  async captureScreenshot() {
    try {
      const canvas = await html2canvas(document.body);
      const data = canvas.toDataURL("image/png").split(",")[1];
      return { success: true, data };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async clickElement(x, y) {
    try {
      const element = document.elementFromPoint(x, y);
      if (element) {
        element.click();
        return { success: true };
      }
      return { success: false, error: "No element at coordinates" };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async typeText(text) {
    try {
      const activeElement = document.activeElement;
      if (activeElement && (activeElement.tagName === "INPUT" || activeElement.tagName === "TEXTAREA")) {
        activeElement.value = text;
        activeElement.dispatchEvent(new Event("input", { bubbles: true }));
        return { success: true };
      }
      return { success: false, error: "No input focused" };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async sendResponse(requestId, result) {
    try {
      await fetch(`${SERVER_URL}/api/extension/response`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id: requestId,
          response: result
        })
      });
    } catch (e) {
      console.error("Failed to send response:", e);
    }
  }
}

const client = new ExtensionClient();
client.start();
```

✅ **Check**: Console shows `✅ Connected to server`


---

## Step 4: Test with Job URL (3 min)

1. Open http://localhost:5000
2. Click **Apply** tab
3. Scroll to **DEBUG** section
4. Paste a LinkedIn job URL:
   ```
   https://www.linkedin.com/jobs/view/4414565663/
   ```
5. Click **Run Apply**
6. Watch the dashboard for progress

Expected sequence:
```
[session_start]
[job_started] - Navigating to job...
[screenshot taken] - Analyzing form...
[job_success] - Application submitted!
[session_done]
```

✅ **Check**: See success or "marked as manual" in dashboard


---

## Step 5: Verify Event Streaming (Optional)

In a new terminal:
```bash
curl -N http://localhost:5000/api/apply/stream
```

You should see events as they happen:
```json
{"type":"session_start","timestamp":"2026-05-26T..."}
{"type":"job_started","job_url":"https://..."}
{"type":"job_success","success":1}
{"type":"session_done","success":1,"manual":0,"failed":0}
```

✅ **Check**: Events appear in real-time


---

## Troubleshooting

### "Cannot reach localhost:5000"
- [ ] Dashboard still running in Step 1?
- [ ] Try: `curl http://localhost:5000` (should show HTML)
- [ ] Check firewall isn't blocking port 5000

### "❌ Ping failed" in console
- [ ] Dashboard running? (check Terminal from Step 1)
- [ ] Extension code pasted in console? (did it say `🚀`?)
- [ ] Open DevTools → Network tab, check `/api/extension/ping` request

### "No commands to execute"
- [ ] Refresh dashboard page (Step 4, click tab again)
- [ ] Make sure you actually clicked "Run Apply"
- [ ] Check dashboard Python console for errors

### Extension not responding to commands
- [ ] Verify JavaScript client is still running (check console)
- [ ] Check if website allows clicking (might have iframes)
- [ ] Try simpler test first (just navigation)

---

## Success Indicators

When everything works, you'll see:

1. ✅ Dashboard loads at http://localhost:5000
2. ✅ Console shows `✅ Connected to server`
3. ✅ Running "Test Apply" with LinkedIn URL completes
4. ✅ Dashboard shows "Job applied successfully" or "Manual review needed"
5. ✅ Events stream shows real-time updates

---

## Next Actions After Success

- [ ] Test with multiple job URLs
- [ ] Monitor form-filling accuracy
- [ ] Check dashboard Apply tab for results
- [ ] Review event logs for any errors
- [ ] Adjust form-filling strategy if needed

---

## Documentation Reference

- 📖 **READY_TO_TEST.md** - Detailed step-by-step guide
- 📖 **INTEGRATION_SUMMARY.md** - Technical architecture
- 📖 **INTEGRATION_COMPLETE.md** - Complete reference
- 🧪 **test_integration.py** - Validation script
- 🐛 **EXTENSION_SERVER_INTEGRATION.md** - Low-level technical details

---

**Time to complete: ~15 minutes**

**Current status: Ready to test! 🚀**
