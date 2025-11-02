import os
import requests
import time
from flask import Flask, request, render_template, jsonify
from threading import Thread, Lock
import logging
from datetime import datetime

# Flask app setup
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables
task_status = {
    'running': False,
    'progress': 0,
    'total_messages': 0,
    'sent_messages': 0,
    'failed_messages': 0,
    'current_message': '',
    'start_time': None,
    'end_time': None
}
status_lock = Lock()

def send_facebook_comment(access_token, post_id, message):
    """
    Send a single comment to Facebook post using Graph API
    """
    try:
        url = f"https://graph.facebook.com/v19.0/{post_id}/comments"
        
        payload = {
            'message': message,
            'access_token': access_token
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        response_data = response.json()
        
        if response.status_code == 200 and 'id' in response_data:
            logger.info(f"✅ Comment sent successfully: {message[:50]}...")
            return True
        else:
            error_msg = response_data.get('error', {}).get('message', 'Unknown error')
            logger.error(f"❌ Facebook API Error: {error_msg}")
            return False
            
    except Exception as e:
        logger.error(f"❌ Network error: {str(e)}")
        return False

def process_messages(access_token, post_id, messages, delay_seconds=10):
    """
    Process all messages with delay between each
    """
    global task_status
    
    with status_lock:
        task_status.update({
            'running': True,
            'progress': 0,
            'total_messages': len(messages),
            'sent_messages': 0,
            'failed_messages': 0,
            'current_message': 'Starting...',
            'start_time': datetime.now().isoformat(),
            'end_time': None
        })
    
    try:
        for index, message in enumerate(messages):
            if not message.strip():
                continue
                
            with status_lock:
                task_status['current_message'] = f"Sending: {message[:50]}..."
                task_status['progress'] = int((index / len(messages)) * 100)
            
            logger.info(f"📤 Sending message {index + 1}/{len(messages)}: {message[:50]}...")
            
            # Send the comment
            success = send_facebook_comment(access_token, post_id, message)
            
            with status_lock:
                if success:
                    task_status['sent_messages'] += 1
                else:
                    task_status['failed_messages'] += 1
                task_status['progress'] = int(((index + 1) / len(messages)) * 100)
            
            # Stop if task was cancelled
            with status_lock:
                if not task_status['running']:
                    break
            
            # Wait before next message (except for the last one)
            if index < len(messages) - 1:
                for i in range(delay_seconds):
                    time.sleep(1)
                    # Check if task was cancelled during delay
                    with status_lock:
                        if not task_status['running']:
                            break
            
        with status_lock:
            task_status['running'] = False
            task_status['end_time'] = datetime.now().isoformat()
            task_status['current_message'] = 'Completed!'
            
        logger.info("🎉 All messages processed!")
        
    except Exception as e:
        with status_lock:
            task_status['running'] = False
            task_status['end_time'] = datetime.now().isoformat()
            task_status['current_message'] = f'Error: {str(e)}'
        logger.error(f"❌ Task failed: {str(e)}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_task():
    global task_status
    
    # Check if task is already running
    with status_lock:
        if task_status['running']:
            return jsonify({'success': False, 'error': 'Task is already running!'})
    
    try:
        # Get form data
        access_token = request.form.get('access_token', '').strip()
        post_id = request.form.get('post_id', '').strip()
        delay = int(request.form.get('delay', 10))
        
        # Validate inputs
        if not access_token:
            return jsonify({'success': False, 'error': 'Facebook Access Token is required!'})
        if not post_id:
            return jsonify({'success': False, 'error': 'Post ID is required!'})
        
        # Get messages file
        if 'messages_file' not in request.files:
            return jsonify({'success': False, 'error': 'Messages file is required!'})
        
        messages_file = request.files['messages_file']
        if messages_file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected!'})
        
        if not messages_file.filename.endswith('.txt'):
            return jsonify({'success': False, 'error': 'Only .txt files are allowed!'})
        
        # Read and process messages
        content = messages_file.read().decode('utf-8')
        messages = [line.strip() for line in content.split('\n') if line.strip()]
        
        if not messages:
            return jsonify({'success': False, 'error': 'No messages found in file!'})
        
        # Start the task in background thread
        thread = Thread(
            target=process_messages,
            args=(access_token, post_id, messages, delay),
            daemon=True
        )
        thread.start()
        
        return jsonify({
            'success': True, 
            'message': f'Task started with {len(messages)} messages!',
            'total_messages': len(messages)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'})

@app.route('/stop', methods=['POST'])
def stop_task():
    with status_lock:
        task_status['running'] = False
        task_status['current_message'] = 'Stopping...'
    
    return jsonify({'success': True, 'message': 'Task stopping...'})

@app.route('/status')
def get_status():
    with status_lock:
        status = task_status.copy()
    
    return jsonify(status)

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
