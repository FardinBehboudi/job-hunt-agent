# Claude in Chrome Extension ↔ Server Integration

This guide explains how to connect Claude in Chrome extension (running in your browser) with the job application server (dashboard).

## 🔗 Architecture

```
Claude in Chrome Extension (Browser)
    ↓
HTTP/WebSocket Connection
    ↓
Dashboard Server (localhost:5000)
    ↓
apply_agent.py ← → chrome_extension_bridge.py
```

## 📱 Step 1: Add Extension Bridge Endpoints to Dashboard

Add these Flask routes to `dashboard/dashboard.py` (around line 5280, before the `if __name__ == "__main__":`):

```python
# ═══ CLAUDE IN CHROME EXTENSION BRIDGE ═══

from chrome_extension_bridge import get_bridge

@app.route("/api/extension/ping", methods=["POST"])
def api_extension_ping():
    """Extension sends heartbeat to confirm it's alive."""
    try:
        data = request.get_json() or {}
        session_id = data.get("session_id", "")
        bridge = get_bridge()
        result = bridge.handle_extension_ping(session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extension/commands", methods=["GET"])
def api_extension_get_commands():
    """Extension polls for pending commands from server."""
    try:
        bridge = get_bridge()
        commands = bridge.get_pending_commands()
        return jsonify({"commands": commands})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/extension/response", methods=["POST"])
def api_extension_send_response():
    """Extension sends back command response."""
    try:
        data = request.get_json() or {}
        request_id = data.get("id", "")
        response = data.get("response", {})
        
        bridge = get_bridge()
        bridge.handle_extension_response(request_id, response)
        
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extension/session", methods=["GET"])
def api_extension_get_session():
    """Get extension session info."""
    try:
        bridge = get_bridge()
        return jsonify({
            "session_id": bridge.session_id,
            "connected": bridge.is_connected,
            "timestamp": datetime.utcnow().isoformat()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

## 🔧 Step 2: Create Browser Extension Integration Script

Create a new file: `browser_extension_script.js`

This script runs in the Claude in Chrome extension's content script:

```javascript
// browser_extension_script.js
// Run this in Claude in Chrome extension console

const SERVER_URL = "http://localhost:5000";
const SESSION_ID = "default"; // Or get from extension storage

class ExtensionClient {
  constructor() {
    this.sessionId = SESSION_ID;
    this.pollInterval = 1000; // 1 second
  }

  async start() {
    console.log("🚀 Extension Bridge starting...");
    
    // Send initial ping
    await this.ping();
    
    // Start polling for commands
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
          
        case "select":
          result = await this.selectDropdown(cmd.params.selector, cmd.params.value);
          break;
          
        case "upload_file":
          result = await this.uploadFile(cmd.params.file_path, cmd.params.selector);
          break;
          
        case "get_url":
          result = { success: true, url: window.location.href };
          break;
          
        default:
          result = { success: false, error: "Unknown command" };
      }
    } catch (e) {
      result = { success: false, error: e.message };
    }
    
    // Send response back to server
    await this.sendResponse(cmd.id, result);
  }

  async captureScreenshot() {
    try {
      // Use html2canvas or similar to capture full page
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

  async selectDropdown(selector, value) {
    try {
      const select = document.querySelector(selector);
      if (select) {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        return { success: true };
      }
      return { success: false, error: "Dropdown not found" };
    } catch (e) {
      return { success: false, error: e.message };
    }
  }

  async uploadFile(filePath, selector) {
    try {
      // This requires special handling - return instructions
      return {
        success: false,
        error: "File upload requires user action or special permission"
      };
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

// Start the client
const client = new ExtensionClient();
client.start();
```

## 🚀 Step 3: How to Use

### 3a. Start Dashboard
```bash
python dashboard/dashboard.py
```

### 3b. Open Claude in Chrome
1. Open Chrome
2. Go to any webpage
3. Open Claude in Chrome extension
4. Click on it to activate

### 3c. Inject Extension Bridge Script
In Claude in Chrome's console, paste the `browser_extension_script.js` code above.

### 3d. Run a Job Application Test
1. Go to dashboard → DEBUG section
2. Paste a LinkedIn job URL
3. Click "Run Apply"
4. Watch the extension execute the commands

## 📊 Message Flow

### Extension Startup
```
Extension: GET /api/extension/session
Server: {"session_id": "abc123", "connected": false}

Extension: POST /api/extension/ping
Server: {"ok": true}
Extension state: ✅ CONNECTED
```

### Command Execution
```
Server sends: navigate to "https://linkedin.com/jobs/view/123"
Extension polls: GET /api/extension/commands
Server responds: [{command: "navigate", params: {url: "..."}}]
Extension executes: window.location = "..."
Extension sends: POST /api/extension/response with result
```

## ⚠️ Limitations

1. **File Upload**: Requires user action (cannot programmatically upload)
2. **CORS**: May need proxy for cross-origin requests
3. **Permissions**: Extension must have permission for the domain
4. **Security**: Never share session_id publicly

## 🔐 Security Notes

- Session IDs are random (UUID)
- Commands are queued in memory (ephemeral)
- No authentication needed for localhost
- Add authentication for production:

```python
# Add to endpoints
@app.before_request
def check_auth():
    token = request.headers.get("X-Extension-Token")
    if token != os.getenv("EXTENSION_TOKEN"):
        return jsonify({"error": "Unauthorized"}), 401
```

## 🐛 Troubleshooting

### Extension not connecting
- Check dashboard is running on port 5000
- Check browser console for CORS errors
- Verify SESSION_ID matches

### Commands not executing
- Check extension console for errors
- Verify coordinates are correct
- Ensure element exists on page

### Screenshot failing
- Install html2canvas library in extension
- Check page is fully loaded
- May fail on cross-origin content

---

**Next Steps:**
1. Add the Flask routes to dashboard.py
2. Inject the JavaScript into Claude in Chrome
3. Test with the DEBUG section
4. Monitor the event stream
