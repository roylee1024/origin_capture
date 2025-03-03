from flask import Flask, request, jsonify
from functools import wraps
from playwright.sync_api import sync_playwright
import time
from urllib.parse import urlparse
import tldextract
import base64
import threading

app = Flask(__name__)

# 基本认证配置
USERS = {
    "admin": "password123"  # 示例用户名和密码
}

def check_auth(username, password):
    """验证用户名和密码是否正确"""
    return username in USERS and USERS[username] == password

def authenticate():
    """返回一个401错误，提示客户端进行认证"""
    return jsonify({"error": "Authentication required"}), 401, {
        'WWW-Authenticate': 'Basic realm="Login Required"'
    }

def requires_auth(f):
    """装饰器：要求HTTP基本认证"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

def extract_domain(url):
    """
    从URL中提取域名
    
    参数:
    url (str): URL字符串
    
    返回:
    str: 域名，如果不是有效域名则返回None
    """
    try:
        # 处理blob URL特殊情况
        if url.startswith('blob:'):
            url = url.split(':', 1)[1]
        
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        
        # 检查域名是否有效（至少包含一个点，不是空字符串）
        if domain and '.' in domain:
            return domain
        return None
    except:
        return None

def capture_requests_playwright(url, timeout=30):
    """
    使用Playwright捕获网页所有HTTP请求和链接
    
    参数:
    url (str): 要分析的网页URL
    timeout (int): 等待页面加载的超时时间（秒）
    
    返回:
    tuple: (链接列表, HTTP请求列表)
    """
    all_requests = []
    
    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        
        # 监听所有请求
        page.on("request", lambda request: all_requests.append(request.url))
        
        try:
            # 访问URL
            page.goto(url)
            
            # 等待页面加载和执行JavaScript
            time.sleep(timeout)
            
            # 提取<a>标签链接
            links = page.eval_on_selector_all("a[href]", """
                elements => elements.map(el => el.href)
            """)
            
            # 合并并去重
            all_urls = list(set(all_requests))
            
            return links, all_urls
            
        finally:
            # 关闭浏览器
            browser.close()

def analyze_domains(url, timeout=30):
    """分析URL并提取所有主域名"""
    links, all_requests = capture_requests_playwright(url, timeout)
    
    # 提取并打印所有唯一域名
    all_domains = set()
    invalid_urls = []
    
    # 从所有链接中提取域名
    for link in links:
        domain = extract_domain(link)
        if domain:
            all_domains.add(domain)
        elif link and not link.startswith('javascript:') and not link.startswith('data:'):
            invalid_urls.append(link)
    
    # 从所有请求中提取域名
    for req in all_requests:
        domain = extract_domain(req)
        if domain:
            all_domains.add(domain)
        elif req and not req.startswith('javascript:') and not req.startswith('data:'):
            invalid_urls.append(req)
    
    # 转换为排序列表以便显示
    sorted_domains = sorted(list(all_domains))

    # 提取主域名（忽略子域名）
    main_domains = set()
    for domain in sorted_domains:
        # 使用tldextract库来正确处理域名
        extract_result = tldextract.extract(domain)
        # 组合域名和顶级域名组成主域名
        main_domain = f"{extract_result.domain}.{extract_result.suffix}"
        if extract_result.suffix:  # 确保有有效的顶级域名
            main_domains.add(main_domain)

    # 转为排序列表
    sorted_main_domains = sorted(list(main_domains))
    
    return {
        "links_count": len(links),
        "requests_count": len(all_requests),
        "main_domains": sorted_main_domains,
        "main_domains_count": len(sorted_main_domains),
        "invalid_urls": invalid_urls[:10],  # 只返回前10个无效URL
        "invalid_urls_count": len(invalid_urls)
    }

# 限制并发请求的线程数
MAX_CONCURRENT_REQUESTS = 5
semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)

@app.route('/analyze', methods=['POST'])
@requires_auth
def analyze_url():
    """分析URL并返回主域名列表的API端点"""
    data = request.json
    
    if not data or 'url' not in data:
        return jsonify({"error": "Missing 'url' in request body"}), 400
    
    url = data['url']
    timeout = data.get('timeout', 30)  # 默认超时时间为30秒
    
    # 使用信号量限制并发请求
    if not semaphore.acquire(blocking=False):
        return jsonify({"error": "Server is busy, please try again later"}), 503
    
    try:
        result = analyze_domains(url, timeout)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        semaphore.release()

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000, debug=False)