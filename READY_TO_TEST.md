# 🚀 Ready to Test - Claude in Chrome Extension Integration

## ✅ Integration Complete

All components have been successfully integrated:
- ✅ Flask dashboard updated with 4 extension bridge endpoints
- ✅ ExtensionBridge singleton communication layer
- ✅ apply_agent.py integrated with browser extension
- ✅ Event streaming system in place
- ✅ All Python syntax validated

## 🎯 Quick Start (5 minutes)

### Terminal 1: Start Dashboard
```bash
cd C:\Users\f_beh\Projects\claude\job-hunt-agent
python dashboard/dashboard.py
```
✅ Server running at http://localhost:5000

### Terminal 2: (Optional) Monitor Events
```bash
curl -N http://localhost:5000/api/apply/stream
```

### Browser: Setup Extension Connection

1. **Open Claude in Chrome**
   - Launch Chrome → Any website → Click Claude in Chrome extension

2. **Inject Extension Bridge**
   - Press `F12` to open DevTools
   - Go to **Console** tab
   - Paste this code:

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

3. **Verify Connection**
   - Console should show: `✅ Connected to server`
   - Dashboard console should show connection accepted

### Test with Job Application

1. **Go to Dashboard**
   - Open http://localhost:5000
   - Click **Apply** tab
   - Scroll to **DEBUG** section

2. **Run Test Application**
   - Paste a LinkedIn job URL:
     ```
     https://www.linkedin.com/jobs/view/4414565663/
     ```
   - Click **Run Apply**
   - Watch extension execute commands in real-time

3. **Monitor Events** (Terminal 2)
   ```
   {"type": "session_start"}
   {"type": "job_started"}
   {"type": "job_success", "success": 1}
   {"type": "session_done"}
   ```

## 📋 What Gets Executed

When you test with a LinkedIn job URL:

1. **Extension navigates** to the job URL
2. **Extension takes screenshot** of the job posting
3. **Claude analyzes** the job description
4. **Extension clicks** on "Apply" or "Easy Apply" button
5. **Extension fills form** with your resume & profile
6. **Extension submits** application
7. **Dashboard reports** success/failure

## ⚠️ Troubleshooting

### Extension not connecting?
```bash
# Check server is running
curl http://localhost:5000/api/extension/session

# Should return:
# {"session_id": "...", "connected": false, "timestamp": "..."}
```

### Commands not executing?
- Check browser console for JavaScript errors
- Verify job page is fully loaded
- Try a simpler action first (navigate only)

### Screenshot not working?
- `html2canvas` library may not be available in extension
- Try without screenshot first

## 📚 Files Involved

```
C:\Users\f_beh\Projects\claude\job-hunt-agent\
├── dashboard/
│   └── dashboard.py          ← 4 new Flask endpoints added
├── chrome_extension_bridge.py ← Communication layer
├── apply_agent.py            ← Uses ExtensionBridge
├── READY_TO_TEST.md          ← This file
└── test_integration.py        ← Validation script
```

## 🎉 Success Indicators

✅ Dashboard starts without errors
✅ Console shows "✅ Connected to server"
✅ test_integration.py shows all PASS
✅ Events stream shows real-time updates
✅ Job application completes or marks as manual

---

**You're ready to test! Start with step 1 above.** 🚀
