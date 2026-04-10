// Cloudflare Worker 脚本：E-Hentai 搜索代理
// 部署到: https://dash.cloudflare.com/

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    
    // 仅允许 POST 请求（安全）
    if (request.method !== 'POST') {
      return new Response('Method not allowed', { status: 405 });
    }
    
    // 解析请求体
    const body = await request.json();
    const { keyword, page = 1, cookies = '', debug = false, baseUrl = 'https://e-hentai.org' } = body;
    
    if (!keyword) {
      return new Response(JSON.stringify({ error: 'Missing keyword' }), { status: 400 });
    }
    
    try {
      // 构建搜索 URL - 支持自定义基础 URL（e-hentai.org 或 exhentai.org）
      const searchUrl = new URL(baseUrl + '/');
      searchUrl.searchParams.append('f_search', keyword);
      if (page > 1) {
        searchUrl.searchParams.append('page', page.toString());
      }
      
      // 构建请求头
      const headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': baseUrl + '/',
      };
      
      if (cookies) {
        headers['Cookie'] = cookies;
      }
      
      // 请求 E-Hentai 或 ExHentai
      const response = await fetch(searchUrl.toString(), {
        method: 'GET',
        headers,
      });
      
      // 即使返回非 200 状态（如 451），也尝试解析内容
      // E-Hentai 经常在 451 状态下返回有效的 HTML 内容
      const html = await response.text();
      
      // 检查是否获得了有效的 HTML
      if (!html || html.length === 0) {
        return new Response(
          JSON.stringify({ error: `Server returned ${response.status} with empty response` }),
          { status: response.status }
        );
      }
      
      // 调试模式：返回 HTML 片段和解析统计
      if (debug) {
        const results = parseSearchResults(html);
        const galleryPattern = /\/g\/(\d+)\/([a-f0-9]+)\//gi;
        const galleryMatches = html.match(galleryPattern) || [];
        
        // 获取 HTML 样本
        const gidMatch = html.match(/\/g\/(\d+)\/([a-f0-9]+)\//);
        let htmlSample = '';
        if (gidMatch) {
          const startIdx = Math.max(0, gidMatch.index - 200);
          const endIdx = Math.min(html.length, gidMatch.index + 500);
          htmlSample = html.substring(startIdx, endIdx);
        }
        
        // 尝试提取前几个标题用于调试
        const titlePattern = /href="\/g\/\d+\/[a-f0-9]+\/[^"]*"[^>]*>([^<]+)<\/a>/gi;
        const titleMatches = [];
        let titleMatch;
        let matchCount = 0;
        while ((titleMatch = titlePattern.exec(html)) !== null && matchCount < 5) {
          const cleanedTitle = cleanTitle(titleMatch[1]);
          if (cleanedTitle) {
            titleMatches.push(cleanedTitle);
          }
          matchCount++;
        }
        
        return new Response(JSON.stringify({
          debug: true,
          htmlSize: html.length,
          galleryLinksFound: galleryMatches.length,
          resultsExtracted: results.length,
          firstFewTitles: titleMatches,
          gidListPresent: /var gid_list = \[/i.test(html),
          itgTablePresent: /class="itg"/i.test(html),
          htmlSample: htmlSample,
          searchPattern: /href="\/g\/\d+\/[a-f0-9]+\/[^"]*"[^>]*>([^<]+)<\/a>/gi.test(html) ? 'matches' : 'no-matches'
        }, null, 2), {
          headers: { 'Content-Type': 'application/json; charset=utf-8' },
        });
      }
      
      // 解析 HTML（提取搜索结果）
      const results = parseSearchResults(html);
      
      return new Response(JSON.stringify({
        success: true,
        count: results.length,
        results: results,
      }), {
        headers: { 'Content-Type': 'application/json' },
      });
      
    } catch (error) {
      return new Response(JSON.stringify({
        error: error.message,
      }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }
  },
};

// 解析搜索结果的辅助函数
function parseSearchResults(html) {
  const results = [];
  
  // 方式 1: 寻找 JavaScript 中 gid_list 的数据结构（最准确）
  const gidListMatch = html.match(/var gid_list = \[([\s\S]*?)\];/i);
  if (gidListMatch) {
    try {
      const items = eval('[' + gidListMatch[1] + ']');
      for (const item of items) {
        if (Array.isArray(item) && item.length >= 3) {
          results.push({
            gid: String(item[0]),
            token: String(item[1]),
            title: cleanTitle(item[2] || ''),
            category: item[3] ? cleanCategory(item[3]) : '',
            rating: typeof item[4] === 'number' ? item[4] : -1,
            url: `https://e-hentai.org/g/${item[0]}/${item[1]}/`,
          });
        }
      }
      if (results.length > 0) return results;
    } catch (e) {
      console.error('gid_list parsing error:', e);
    }
  }
  
  // 方式 2: 使用正则表达式从 HTML 中提取所有图集链接（适应多种布局）
  // 匹配模式: href="https://exhentai.org/g/(\d+)/([a-f0-9]+)/"
  // 标题在 <div class="glink"> 或 <div class="glname glink"> 内
  const galleryPattern = /href="(?:https?:)?\/\/e(?:x)?hentai\.org\/g\/(\d+)\/([a-f0-9]+)\/[^"]*"[^>]*>\s*<div[^>]*class="[^"]*glink[^"]*"[^>]*>([^<]+)<\/div>/gi;
  let match;
  const seen = new Set();
  
  while ((match = galleryPattern.exec(html)) !== null) {
    const gid = match[1];
    const token = match[2];
    const title = cleanTitle(match[3]);
    
    // 去重
    const key = `${gid}:${token}`;
    if (seen.has(key)) continue;
    seen.add(key);
    
    // 跳过空标题
    if (!title) continue;
    
    results.push({
      gid,
      token,
      title,
      category: '',
      rating: -1,
      url: `https://exhentai.org/g/${gid}/${token}/`,
    });
  }
  
  if (results.length > 0) return results;
  
  // 备选方式 2b: 旧版格式或其他布局 - 标题直接在 <a> 标签内
  const altPattern = /href="(?:https?:)?\/\/e(?:x)?hentai\.org\/g\/(\d+)\/([a-f0-9]+)\/[^"]*"[^>]*>([^<]+)<\/a>/gi;
  
  while ((match = altPattern.exec(html)) !== null) {
    const gid = match[1];
    const token = match[2];
    const title = cleanTitle(match[3]);
    
    const key = `${gid}:${token}`;
    if (seen.has(key)) continue;
    seen.add(key);
    
    if (!title) continue;
    
    results.push({
      gid,
      token,
      title,
      category: '',
      rating: -1,
      url: `https://exhentai.org/g/${gid}/${token}/`,
    });
  }
  
  // 方式 3: 解析 .itg 表格行（备选方案 - 旧版布局）
  const itgMatch = html.match(/<table[^>]*class="itg"[^>]*>([\s\S]*?)<\/table>/i);
  if (itgMatch) {
    const table = itgMatch[1];
    
    // 查找所有 <tr> 行
    const rowMatches = table.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi);
    
    for (const rowMatch of rowMatches) {
      const row = rowMatch[1];
      
      // 跳过表头行（包含 <th>）
      if (/<th/i.test(row)) continue;
      
      // 提取代表库 URL 的链接
      const urlMatch = row.match(/\/g\/(\d+)\/([a-f0-9]{10})\//i);
      if (!urlMatch) continue;
      
      const gid = urlMatch[1];
      const token = urlMatch[2];
      
      // 提取标题（通常在第一个或第二个 <td> 中的链接）
      const titleMatch = row.match(/<a[^>]*href="[^"]*\/g\/\d+\/[a-f0-9]{10}[^"]*"[^>]*>([^<]+)<\/a>/i);
      const title = titleMatch ? cleanTitle(titleMatch[1]) : '(无标题)';
      
      if (title === '(无标题)') continue;
      
      results.push({
        gid,
        token,
        title,
        category: '',
        rating: -1,
        url: `https://e-hentai.org/g/${gid}/${token}/`,
      });
    }
  }
  
  return results;
}

function cleanTitle(title) {
  if (!title) return '';
  
  // 首先处理所有 HTML 实体
  const entities = {
    '&nbsp;': ' ',
    '&lt;': '<',
    '&gt;': '>',
    '&amp;': '&',
    '&quot;': '"',
    '&#39;': "'",
    '&apos;': "'",
    '&#x27;': "'",
  };
  
  let result = title;
  
  // 处理具名实体
  for (const [entity, char] of Object.entries(entities)) {
    result = result.replace(new RegExp(entity.replace(/&/g, '\\&'), 'gi'), char);
  }
  
  // 处理数字实体 &#123;
  result = result.replace(/&#(\d+);/g, (match, code) => {
    return String.fromCharCode(parseInt(code));
  });
  
  // 处理十六进制实体 &#x1a;
  result = result.replace(/&#x([0-9a-f]+);/gi, (match, code) => {
    return String.fromCharCode(parseInt(code, 16));
  });
  
  // 移除 HTML 标签
  result = result.replace(/<[^>]*>/g, '');
  
  // 合并多个空格
  result = result.replace(/\s+/g, ' ');
  
  return result.trim();
}

function cleanCategory(category) {
  if (!category) return '';
  
  return category
    .replace(/<[^>]*>/g, '') // 移除 HTML 标签
    .replace(/&nbsp;/gi, ' ')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/\s+/g, ' ') // 合并多个空格
    .trim();
}
