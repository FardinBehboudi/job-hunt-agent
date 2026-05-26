"""
chrome_extension_bridge.py

Communication layer between server (apply_agent.py) and Claude in Chrome extension.

The extension runs in the browser and communicates back to this server via HTTP/WebSocket.
This allows apply_agent.py to send commands to the extension and receive results.
"""

import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any
from queue import Queue
import uuid

log = logging.getLogger(__name__)


class ExtensionBridge:
    """
    Manages communication between server and Claude in Chrome extension.
    
    Usage:
        bridge = ExtensionBridge()
        await bridge.navigate("https://linkedin.com/jobs/view/123")
        screenshot = await bridge.take_screenshot()
        await bridge.click(100, 200)
        await bridge.type("text to type")
    """

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session_id = str(uuid.uuid4())[:8]
        self.request_queue: Queue = Queue()
        self.response_cache: Dict[str, Any] = {}
        self.is_connected = False
        self.last_heartbeat = time.time()
        
        log.info(f"ExtensionBridge initialized with session {self.session_id}")

    async def wait_for_extension(self, max_wait: int = 60) -> bool:
        """Wait for the extension to connect."""
        log.info("⏳ Waiting for Claude in Chrome extension to connect...")
        log.info("   Please ensure:")
        log.info("   1. Claude in Chrome extension is installed")
        log.info("   2. You've opened Claude in your browser")
        log.info("   3. The extension is connected to this server")
        
        start = time.time()
        while time.time() - start < max_wait:
            # Check if extension has pinged us
            if self.is_connected:
                log.info("✅ Claude in Chrome extension connected!")
                return True
            await asyncio.sleep(1)
        
        log.warning("❌ Claude in Chrome extension did not connect within timeout")
        return False

    async def navigate(self, url: str) -> bool:
        """Send navigate command to extension."""
        log.info(f"📍 Navigating to {url}")
        result = await self._send_command("navigate", {"url": url})
        return result.get("success", False)

    async def take_screenshot(self) -> Optional[bytes]:
        """Get screenshot from extension."""
        log.info("📸 Taking screenshot...")
        result = await self._send_command("screenshot", {})
        
        if result.get("success") and result.get("data"):
            # Decode base64 screenshot
            import base64
            try:
                return base64.b64decode(result["data"])
            except Exception as e:
                log.error(f"Failed to decode screenshot: {e}")
                return None
        return None

    async def click(self, x: int, y: int) -> bool:
        """Click at coordinates."""
        log.info(f"🖱️  Clicking at ({x}, {y})")
        result = await self._send_command("click", {"x": x, "y": y})
        return result.get("success", False)

    async def type(self, text: str) -> bool:
        """Type text."""
        log.info(f"⌨️  Typing: {text[:50]}...")
        result = await self._send_command("type", {"text": text})
        return result.get("success", False)

    async def select_dropdown(self, selector: str, value: str) -> bool:
        """Select from dropdown."""
        log.info(f"📋 Selecting '{value}' from dropdown")
        result = await self._send_command("select", {
            "selector": selector,
            "value": value
        })
        return result.get("success", False)

    async def upload_file(self, file_path: str, input_selector: str) -> bool:
        """Upload file to file input."""
        log.info(f"📁 Uploading file: {file_path}")
        result = await self._send_command("upload_file", {
            "file_path": file_path,
            "selector": input_selector
        })
        return result.get("success", False)

    async def get_current_url(self) -> Optional[str]:
        """Get current browser URL."""
        result = await self._send_command("get_url", {})
        return result.get("url")

    async def wait_for_element(self, selector: str, timeout: int = 10) -> bool:
        """Wait for element to appear."""
        log.info(f"⏳ Waiting for element: {selector}")
        result = await self._send_command("wait_element", {
            "selector": selector,
            "timeout": timeout
        })
        return result.get("success", False)

    async def close_modal(self) -> bool:
        """Close any open modal."""
        log.info("❌ Closing modal...")
        result = await self._send_command("close_modal", {})
        return result.get("success", False)

    # ─── Internal Methods ───

    async def _send_command(self, command: str, params: dict) -> dict:
        """Send command to extension and wait for response."""
        request_id = str(uuid.uuid4())
        
        message = {
            "id": request_id,
            "session": self.session_id,
            "command": command,
            "params": params,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        log.debug(f"Sending command: {command} (id: {request_id})")
        
        # Add to request queue (for the extension to fetch)
        self.request_queue.put(message)
        
        # Wait for response
        start = time.time()
        while time.time() - start < self.timeout:
            if request_id in self.response_cache:
                response = self.response_cache.pop(request_id)
                log.debug(f"Got response: {response}")
                return response
            await asyncio.sleep(0.1)
        
        log.error(f"Command timeout: {command} (id: {request_id})")
        return {"success": False, "error": "timeout"}

    def handle_extension_ping(self, session_id: str) -> dict:
        """Called when extension pings server to say it's alive."""
        if session_id == self.session_id:
            self.is_connected = True
            self.last_heartbeat = time.time()
            log.debug("Extension heartbeat received")
            return {"ok": True}
        return {"ok": False, "error": "session mismatch"}

    def handle_extension_response(self, request_id: str, response: dict) -> None:
        """Called when extension sends back a command response."""
        self.response_cache[request_id] = response
        log.debug(f"Response cached: {request_id}")

    def get_pending_commands(self) -> list:
        """Get all pending commands for the extension."""
        commands = []
        while not self.request_queue.empty():
            try:
                commands.append(self.request_queue.get_nowait())
            except:
                break
        return commands


# Global bridge instance
_bridge: Optional[ExtensionBridge] = None


def get_bridge() -> ExtensionBridge:
    """Get or create the extension bridge."""
    global _bridge
    if _bridge is None:
        _bridge = ExtensionBridge()
    return _bridge


def init_bridge() -> ExtensionBridge:
    """Initialize the bridge."""
    global _bridge
    _bridge = ExtensionBridge()
    return _bridge
