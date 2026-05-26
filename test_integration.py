#!/usr/bin/env python3
"""Test integration script."""

import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

def test_chrome_extension_bridge():
    print("Testing chrome_extension_bridge.py...")
    try:
        from chrome_extension_bridge import get_bridge
        bridge1 = get_bridge()
        bridge2 = get_bridge()
        assert bridge1 is bridge2
        print("  PASS: ExtensionBridge singleton works")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

def test_apply_agent():
    print("\nTesting apply_agent.py...")
    try:
        with open(project_root / "apply_agent.py", "r", encoding="utf-8") as f:
            content = f.read()
        assert "from chrome_extension_bridge import get_bridge" in content
        print("  PASS: apply_agent uses ExtensionBridge")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

def test_dashboard_endpoints():
    print("\nTesting dashboard.py...")
    try:
        with open(project_root / "dashboard" / "dashboard.py", "r", encoding="utf-8") as f:
            content = f.read()
        endpoints = ["/api/extension/ping", "/api/extension/commands", "/api/extension/response", "/api/extension/session"]
        for endpoint in endpoints:
            assert endpoint in content
        print("  PASS: All 4 endpoints found")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

def test_event_system():
    print("\nTesting event system...")
    try:
        from applier import events
        assert hasattr(events, '_event_queue')
        assert hasattr(events, '_emit')
        print("  PASS: Event system working")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Integration Tests")
    print("=" * 60)
    
    results = {
        "Extension Bridge": test_chrome_extension_bridge(),
        "Apply Agent": test_apply_agent(),
        "Dashboard Endpoints": test_dashboard_endpoints(),
        "Event System": test_event_system(),
    }
    
    print("\n" + "=" * 60)
    for test, result in results.items():
        status = "PASS" if result else "FAIL"
        print(f"{test}: {status}")
    
    all_pass = all(results.values())
    print("=" * 60)
    
    if all_pass:
        print("\n✅ All tests passed!")
    else:
        print("\n❌ Some tests failed")
    
    sys.exit(0 if all_pass else 1)
