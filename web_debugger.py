"""
web_debugger.py - Web UI for testing job applications.
"""

import asyncio
import json
import logging
from flask import Flask, render_template_string, request, jsonify
from pathlib import Path
from config_loader import load_config
from apply_agent import apply_to_job

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)

# HTML Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Job Apply Debugger</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; }
        .container { max-width: 900px; margin: 0 auto; padding: 20px; }
        .header { background: #2c3e50; color: white; padding: 30px; border-radius: 8px; margin-bottom: 30px; }
        .header h1 { font-size: 28px; margin-bottom: 10px; }
        .header p { opacity: 0.9; }
        .form-group { background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .form-group label { display: block; margin-bottom: 8px; font-weight: 600; color: #333; }
        .form-group input { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 4px; font-size: 14px; }
        .form-group input:focus { outline: none; border-color: #3498db; box-shadow: 0 0 0 3px rgba(52,152,219,0.1); }
        button { background: #3498db; color: white; padding: 12px 30px; border: none; border-radius: 4px; font-size: 16px; cursor: pointer; font-weight: 600; }
        button:hover { background: #2980b9; }
        button:disabled { background: #bdc3c7; cursor: not-allowed; }
        .result { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-top: 20px; }
        .result.success { border-left: 4px solid #27ae60; }
        .result.error { border-left: 4px solid #e74c3c; }
        .result.loading { border-left: 4px solid #f39c12; }
        .result h2 { margin-bottom: 15px; }
        .result-item { padding: 10px 0; border-bottom: 1px solid #ecf0f1; }
        .result-item:last-child { border-bottom: none; }
        .result-label { font-weight: 600; color: #555; margin-bottom: 5px; }
        .result-value { color: #333; padding-left: 10px; }
        .success-icon { color: #27ae60; }
        .error-icon { color: #e74c3c; }
        .loading-icon { color: #f39c12; }
        .spinner { display: inline-block; width: 20px; height: 20px; border: 3px solid #f3f3f3; border-top: 3px solid #3498db; border-radius: 50%; animation: spin 1s linear infinite; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .config-info { background: #ecf0f1; padding: 15px; border-radius: 4px; margin-bottom: 20px; font-size: 14px; }
        .config-info p { margin: 8px 0; }
        .config-ok { color: #27ae60; }
        .config-error { color: #e74c3c; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🤖 Job Apply Debugger</h1>
            <p>Test Claude in Chrome job applications</p>
        </div>

        <div id="config-status" class="config-info"></div>

        <div class="form-group">
            <label for="job-url">LinkedIn Job URL</label>
            <input type="text" id="job-url" placeholder="https://www.linkedin.com/jobs/view/123456/" value="">
            <button onclick="testJob()" id="test-btn" style="margin-top: 15px;">Test Application</button>
        </div>

        <div id="result-container"></div>
    </div>

    <script>
        // Load config status on page load
        window.onload = function() {
            fetch('/api/config')
                .then(r => r.json())
                .then(data => {
                    let html = '<h3>Configuration Status</h3>';
                    html += '<p><span class="' + (data.has_profile ? 'config-ok' : 'config-error') + '">✓ Profile: ' + data.profile_name + '</span></p>';
                    html += '<p><span class="' + (data.has_resume_en ? 'config-ok' : 'config-error') + '">✓ Resume EN: ' + (data.has_resume_en ? 'Found' : 'Not found') + '</span></p>';
                    html += '<p><span class="' + (data.has_resume_de ? 'config-ok' : 'config-error') + '">✓ Resume DE: ' + (data.has_resume_de ? 'Found' : 'Not found') + '</span></p>';
                    document.getElementById('config-status').innerHTML = html;
                });
        };

        function testJob() {
            const url = document.getElementById('job-url').value;
            if (!url) {
                alert('Please enter a LinkedIn job URL');
                return;
            }

            const btn = document.getElementById('test-btn');
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner"></span> Testing...';

            fetch('/api/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ job_url: url })
            })
            .then(r => r.json())
            .then(data => {
                displayResult(data);
            })
            .catch(err => {
                displayError('Error: ' + err.message);
            })
            .finally(() => {
                btn.disabled = false;
                btn.innerHTML = 'Test Application';
            });
        }

        function displayResult(data) {
            const container = document.getElementById('result-container');
            const success = data.success;
            const html = `
                <div class="result ${success ? 'success' : 'error'}">
                    <h2>${success ? '✅ Success!' : '❌ Failed'}</h2>
                    <div class="result-item">
                        <div class="result-label">Status</div>
                        <div class="result-value">${success ? 'Application submitted' : 'Manual review required'}</div>
                    </div>
                    <div class="result-item">
                        <div class="result-label">Apply Type</div>
                        <div class="result-value">${data.apply_type || 'Unknown'}</div>
                    </div>
                    <div class="result-item">
                        <div class="result-label">Note</div>
                        <div class="result-value">${data.note || 'N/A'}</div>
                    </div>
                    <div class="result-item">
                        <div class="result-label">Timestamp</div>
                        <div class="result-value">${data.timestamp || 'N/A'}</div>
                    </div>
                </div>
            `;
            container.innerHTML = html;
        }

        function displayError(msg) {
            const container = document.getElementById('result-container');
            const html = `<div class="result error"><h2>❌ Error</h2><div class="result-item"><div class="result-value">${msg}</div></div></div>`;
            container.innerHTML = html;
        }
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    """Serve the web UI."""
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/config')
def get_config():
    """Get configuration status."""
    config = load_config()
    profile = config.get("application_profile", {})
    resume_en = config.get("resume_paths", {}).get("en")
    resume_de = config.get("resume_paths", {}).get("de")

    return jsonify({
        "has_profile": bool(profile),
        "profile_name": f"{profile.get('first_name', 'Unknown')} {profile.get('last_name', '')}".strip(),
        "has_resume_en": bool(resume_en),
        "has_resume_de": bool(resume_de),
        "resume_en_path": str(resume_en) if resume_en else None,
        "resume_de_path": str(resume_de) if resume_de else None
    })


@app.route('/api/test', methods=['POST'])
def test_job():
    """Test applying to a job."""
    data = request.json
    job_url = data.get("job_url", "")

    if not job_url:
        return jsonify({"error": "Job URL required"}), 400

    try:
        # Load config
        config = load_config()
        profile = config.get("application_profile", {})
        resume_en = config.get("resume_paths", {}).get("en")

        if not resume_en:
            return jsonify({
                "success": False,
                "apply_type": "Manual Required",
                "note": "Resume not found in ~/Dropbox/CV/resume_en.pdf",
                "timestamp": ""
            }), 200

        # Run test
        result = asyncio.run(apply_to_job(
            job_url=job_url,
            job_title="Debug Test",
            company_name="Test Company",
            application_profile=profile,
            resume_path=resume_en,
            job_description="Testing Claude in Chrome integration"
        ))

        return jsonify(result), 200

    except Exception as e:
        log.error(f"Error: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "apply_type": "Manual Required",
            "note": f"Error: {str(e)}",
            "timestamp": ""
        }), 200


if __name__ == "__main__":
    print("\n" + "="*50)
    print("🌐 Web Debugger Starting...")
    print("="*50)
    print("\n✅ Open your browser and go to:")
    print("   http://localhost:5000")
    print("\n" + "="*50 + "\n")
    app.run(debug=True, host="0.0.0.0", port=5000)
