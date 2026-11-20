/**
 * GEO Growth OS - 主JavaScript文件
 * 全局交互逻辑
 */

document.addEventListener('DOMContentLoaded', function() {
    // 自动隐藏alert消息
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.5s';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });

    // 侧边栏导航高亮
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-item').forEach(item => {
        if (item.getAttribute('href') === currentPath) {
            item.classList.add('active');
        }
    });

    // 数字动画
    animateNumbers();
});

/**
 * 数字滚动动画
 */
function animateNumbers() {
    const numbers = document.querySelectorAll('.stat-value, .comp-stat-value');
    numbers.forEach(el => {
        const finalValue = el.textContent;
        const isPercent = finalValue.includes('%');
        const isCurrency = finalValue.includes('¥');
        const numValue = parseFloat(finalValue.replace(/[^\d.]/g, ''));
        
        if (isNaN(numValue)) return;

        let current = 0;
        const increment = numValue / 30;
        const timer = setInterval(() => {
            current += increment;
            if (current >= numValue) {
                current = numValue;
                clearInterval(timer);
            }
            let display = Math.floor(current).toLocaleString();
            if (isPercent) display += '%';
            if (isCurrency) display = '¥' + display;
            el.textContent = display;
        }, 30);
    });
}

/**
 * 复制文本到剪贴板
 */
function copyToClipboard(text) {
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => {
            showToast('已复制到剪贴板！', 'success');
        });
    } else {
        // 降级方案
        const textarea = document.createElement('textarea');
        textarea.value = text;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        showToast('已复制到剪贴板！', 'success');
    }
}

/**
 * 显示Toast通知
 */
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    toast.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 12px 24px;
        border-radius: 10px;
        color: #fff;
        font-size: 14px;
        font-weight: 500;
        z-index: 9999;
        animation: slideIn 0.3s ease;
        box-shadow: 0 10px 25px rgba(0,0,0,0.15);
    `;
    
    const colors = {
        success: '#10b981',
        error: '#ef4444',
        info: '#3b82f6',
        warning: '#f59e0b'
    };
    toast.style.background = colors[type] || colors.info;

    document.body.appendChild(toast);

    setTimeout(() => {
        toast.style.transition = 'opacity 0.3s, transform 0.3s';
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(20px)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * 格式化数字
 */
function formatNumber(num) {
    if (num >= 10000) {
        return (num / 10000).toFixed(1) + '万';
    }
    return num.toLocaleString();
}

/**
 * 防抖函数
 */
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

/**
 * 关闭所有模态框
 */
function closeAllModals() {
    document.querySelectorAll('.modal').forEach(modal => {
        modal.style.display = 'none';
    });
}

// 点击模态框外部关闭
document.addEventListener('click', function(e) {
    if (e.target.classList.contains('modal')) {
        e.target.style.display = 'none';
    }
});

// ESC键关闭模态框
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeAllModals();
    }
});

// 添加slideIn动画
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from { opacity: 0; transform: translateX(20px); }
        to { opacity: 1; transform: translateX(0); }
    }
`;
document.head.appendChild(style);
