import os
import requests
import time
from flask import Flask, request, render_template, jsonify
from threading import Thread, Lock
import logging
from datetime import datetime
import random
import json

# Flask app setup
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-here')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global variables for multi-task support
tasks = {}
tasks_lock = Lock()

def validate_facebook_token(token):
    """Validate Facebook token before using"""
    try:
        url = f"https://graph.facebook.com/v19.0/me?fields=id,name&access_token={token}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            user_data = response.json()
            return True, {
                'valid': True,
                'user_id': user_data.get('id'),
                'user_name': user_data.get('name', 'Unknown')
            }
        else:
            error_data = response.json()
            error_msg = error_data.get('error', {}).get('message', 'Unknown error')
            return False, f"Token invalid: {error_msg}"
            
    except Exception as e:
        return False, f"Validation error: {str(e)}"

def send_facebook_comment(access_token, post_id, message, task_id):
    """
    Improved Facebook comment sending with better error handling
    """
    try:
        url = f"https://graph.facebook.com/v19.0/{post_id}/comments"
        
        payload = {
            'message': message,
            'access_token': access_token
        }
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json'
        }
        
        response = requests.post(url, data=payload, headers=headers, timeout=30)
        response_data = response.json()
        
        if response.status_code == 200 and 'id' in response_data:
            logger.info(f"✅ [{task_id}] Comment sent: {message[:30]}...")
            return True, "Success"
        else:
            error_msg = response_data.get('error', {}).get('message', 'Unknown error')
            error_code = response_data.get('error', {}).get('code', 'Unknown')
            
            # Update task with specific error
            with tasks_lock:
                if task_id in tasks:
                    tasks[task_id]['last_error'] = f"{error_code}: {error_msg}"
            
            logger.error(f"❌ [{task_id}] Facebook Error {error_code}: {error_msg}")
            
            # Specific error handling
            if error_code == 190:
                return False, "TOKEN_EXPIRED"
            elif error_code == 10:
                return False, "PERMISSION_DENIED"
            elif error_code == 200:
                return False, "PERMISSION_DENIED"
            else:
                return False, error_msg
            
    except Exception as e:
        error_msg = f"Network error: {str(e)}"
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['last_error'] = error_msg
        logger.error(f"❌ [{task_id}] {error_msg}")
        return False, error_msg

def process_messages(task_id, access_tokens, post_id, messages, delay_seconds=10):
    """
    Process messages with multiple tokens and better error handling
    """
    # First validate all tokens
    valid_tokens = []
    token_info = []
    
    logger.info(f"🔍 [{task_id}] Validating {len(access_tokens)} tokens...")
    
    for i, token in enumerate(access_tokens):
        is_valid, validation_result = validate_facebook_token(token)
        if is_valid:
            valid_tokens.append(token)
            token_info.append(f"Token {i+1}: {validation_result['user_name']}")
            logger.info(f"✅ [{task_id}] Token {i+1} valid: {validation_result['user_name']}")
        else:
            logger.warning(f"❌ [{task_id}] Token {i+1} invalid: {validation_result}")
    
    if not valid_tokens:
        with tasks_lock:
            tasks[task_id] = {
                'running': False,
                'progress': 0,
                'total_messages': len(messages),
                'sent_messages': 0,
                'failed_messages': 0,
                'current_message': 'ALL_TOKENS_INVALID',
                'start_time': datetime.now().isoformat(),
                'end_time': datetime.now().isoformat(),
                'active_tokens': 0,
                'last_error': 'No valid tokens found!',
                'current_token_index': 0
            }
        return
    
    # Initialize task with valid tokens
    with tasks_lock:
        tasks[task_id] = {
            'running': True,
            'progress': 0,
            'total_messages': len(messages),
            'sent_messages': 0,
            'failed_messages': 0,
            'current_message': f'Starting with {len(valid_tokens)} valid tokens...',
            'start_time': datetime.now().isoformat(),
            'end_time': None,
            'active_tokens': len(valid_tokens),
            'last_error': None,
            'current_token_index': 0,
            'token_info': token_info
        }
    
    try:
        expired_tokens = set()
        
        for index, message in enumerate(messages):
            if not message.strip():
                continue
                
            # Update current message
            with tasks_lock:
                tasks[task_id]['current_message'] = f"Sending: {message[:50]}..."
                tasks[task_id]['progress'] = int((index / len(messages)) * 100)
            
            logger.info(f"📤 [{task_id}] Sending {index + 1}/{len(messages)}: {message[:50]}...")
            
            # Try all valid tokens for this message
            message_sent = False
            for token_index, token in enumerate(valid_tokens):
                if token in expired_tokens:
                    continue
                    
                with tasks_lock:
                    tasks[task_id]['current_token_index'] = token_index
                
                # Check if task was stopped
                with tasks_lock:
                    if not tasks[task_id]['running']:
                        break
                
                success, error_msg = send_facebook_comment(token, post_id, message, task_id)
                
                if success:
                    with tasks_lock:
                        tasks[task_id]['sent_messages'] += 1
                    message_sent = True
                    break
                elif error_msg == "TOKEN_EXPIRED":
                    logger.warning(f"🔄 [{task_id}] Token {token_index} expired, marking...")
                    expired_tokens.add(token)
                    continue
                else:
                    # Try next token if this one fails
                    continue
            
            if not message_sent:
                with tasks_lock:
                    tasks[task_id]['failed_messages'] += 1
            
            # Update progress
            with tasks_lock:
                tasks[task_id]['progress'] = int(((index + 1) / len(messages)) * 100)
                tasks[task_id]['active_tokens'] = len(valid_tokens) - len(expired_tokens)
            
            # Stop if no more valid tokens
            if len(expired_tokens) >= len(valid_tokens):
                with tasks_lock:
                    tasks[task_id]['last_error'] = "ALL_TOKENS_EXPIRED"
                break
            
            # Stop if task was cancelled
            with tasks_lock:
                if not tasks[task_id]['running']:
                    break
            
            # Wait before next message (except for the last one)
            if index < len(messages) - 1:
                for i in range(delay_seconds):
                    time.sleep(1)
                    with tasks_lock:
                        if not tasks[task_id]['running']:
                            break
        
        # Mark task as completed
        with tasks_lock:
            tasks[task_id]['running'] = False
            tasks[task_id]['end_time'] = datetime.now().isoformat()
            
            if tasks[task_id]['sent_messages'] > 0:
                tasks[task_id]['current_message'] = f"Completed! {tasks[task_id]['sent_messages']}/{tasks[task_id]['total_messages']} sent"
            else:
                tasks[task_id]['current_message'] = "Failed - check token permissions"
            
        logger.info(f"🎉 [{task_id}] Task completed!")
        
    except Exception as e:
        with tasks_lock:
            if task_id in tasks:
                tasks[task_id]['running'] = False
                tasks[task_id]['end_time'] = datetime.now().isoformat()
                tasks[task_id]['current_message'] = f'Error: {str(e)}'
        logger.error(f"❌ [{task_id}] Task failed: {str(e)}")

def generate_task_id():
    """Generate unique task ID"""
    return f"task_{int(time.time())}_{random.randint(1000, 9999)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/validate_token', methods=['POST'])
def validate_token_endpoint():
    """Endpoint to validate token before starting task"""
    token = request.json.get('token', '').strip()
    
    if not token:
        return jsonify({'success': False, 'error': 'Token is required'})
    
    is_valid, result = validate_facebook_token(token)
    
    if is_valid:
        return jsonify({
            'success': True,
            'valid': True,
            'user_id': result['user_id'],
            'user_name': result['user_name']
        })
    else:
        return jsonify({
            'success': False,
            'valid': False,
            'error': result
        })

@app.route('/start', methods=['POST'])
def start_task():
    try:
        # Get form data
        access_tokens_text = request.form.get('access_tokens', '').strip()
        post_id = request.form.get('post_id', '').strip()
        delay = int(request.form.get('delay', 10))
        
        # Validate inputs
        if not access_tokens_text:
            return jsonify({'success': False, 'error': 'Facebook Access Tokens are required!'})
        if not post_id:
            return jsonify({'success': False, 'error': 'Post ID is required!'})
        
        # Parse multiple tokens (comma or newline separated)
        access_tokens = []
        for line in access_tokens_text.split('\n'):
            for token in line.split(','):
                token = token.strip()
                if token and token.startswith('EAAG'):
                    access_tokens.append(token)
        
        if not access_tokens:
            return jsonify({'success': False, 'error': 'No valid Facebook tokens found!'})
        
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
        
        # Generate unique task ID
        task_id = generate_task_id()
        
        # Start the task in background thread
        thread = Thread(
            target=process_messages,
            args=(task_id, access_tokens, post_id, messages, delay),
            daemon=True
        )
        thread.start()
        
        return jsonify({
            'success': True, 
            'message': f'Task {task_id} started with {len(access_tokens)} tokens ({len(messages)} messages)!',
            'task_id': task_id,
            'total_messages': len(messages),
            'total_tokens': len(access_tokens)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'})

@app.route('/stop/<task_id>', methods=['POST'])
def stop_task(task_id):
    with tasks_lock:
        if task_id in tasks:
            tasks[task_id]['running'] = False
            tasks[task_id]['current_message'] = 'Stopping...'
            return jsonify({'success': True, 'message': f'Task {task_id} stopping...'})
    
    return jsonify({'success': False, 'error': 'Task not found!'})

@app.route('/status/<task_id>')
def get_task_status(task_id):
    with tasks_lock:
        if task_id in tasks:
            return jsonify(tasks[task_id])
    
    return jsonify({'error': 'Task not found'})

@app.route('/tasks')
def list_tasks():
    with tasks_lock:
        active_tasks = {tid: task for tid, task in tasks.items() if task['running']}
        completed_tasks = {tid: task for tid, task in tasks.items() if not task['running']}
    
    return jsonify({
        'active_tasks': active_tasks,
        'completed_tasks': completed_tasks,
        'total_tasks': len(tasks)
    })

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
