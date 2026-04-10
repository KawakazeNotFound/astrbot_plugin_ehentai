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
    const { keyword, page = 1, cookies = '', debug = false, baseUrl = 'https://e-hentai.org', rawHtml = false } = body;
    
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
      if (debug === 'full') {
        return new Response(html, { headers: { 'Content-Type': 'text/html' } });
      }
      
      if (debug === true || debug === 'true') {
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
      
      // 如果请求纯 HTML（推荐方法，让客户端自行解析所有元数据）
      if (rawHtml) {
        return new Response(JSON.stringify({
          success: true,
          html: html
        }), {
          headers: { 'Content-Type': 'application/json' },
        });
      }
      
      // 解析 HTML（提取搜索结果）
      const results = parseSearchResults(html, baseUrl);
      
      return new Response(JSON.stringify({
        success: true,
        count: results.length,
        results: results,
        html: debug ? html : undefined // 如果需要，也可以返回原始 HTML
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
function parseSearchResults(html, baseUrl = 'https://e-hentai.org') {
  const results = [];
  
  // 方式 1: 寻找 JavaScript 中 gid_list 的数据结构（最准确）
  const gidListMatch = html.match(/var gid_list = \[([\s\S]*?)\];/i);
  if (gidListMatch) {
    try {
      const items = eval('[' + gidListMatch[1] + ']');
      for (const item of items) {
        if (Array.isArray(item) && item.length >= 3) {
          // 从 gid_list 中 item 结构: [gid, token, title, category, rating, ...]
          // 需要从 HTML 中找到对应的行来提取缩略图
          const gid = String(item[0]);
          const token = String(item[1]);
          const coverUrl = extractCoverUrlFromHtml(html, gid, token, baseUrl);
          
          results.push({
            gid,
            token,
            title: cleanTitle(item[2] || ''),
            category: item[3] ? cleanCategory(item[3]) : '',
            rating: typeof item[4] === 'number' ? item[4] : -1,
            url: `${baseUrl}/g/${gid}/${token}/`,
            cover_url: coverUrl,
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
    
    // 从 HTML 中提取该行的缩略图
    const coverUrl = extractCoverUrlFromHtml(html, gid, token, baseUrl);
    
    results.push({
      gid,
      token,
      title,
      category: '',
      rating: -1,
      url: `${baseUrl}/g/${gid}/${token}/`,
      cover_url: coverUrl,
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
    
    // 从 HTML 中提取该行的缩略图
    const coverUrl = extractCoverUrlFromHtml(html, gid, token, baseUrl);
    
    results.push({
      gid,
      token,
      title,
      category: '',
      rating: -1,
      url: `${baseUrl}/g/${gid}/${token}/`,
      cover_url: coverUrl,
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
      
      // 从该行提取缩略图
      const coverUrl = extractCoverUrlFromHtml(html, gid, token, baseUrl);
      
      results.push({
        gid,
        token,
        title,
        category: '',
        rating: -1,
        url: `${baseUrl}/g/${gid}/${token}/`,
        cover_url: coverUrl,
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

// 从 HTML 中提取特定 gid/token 的缩略图 URL
function extractCoverUrlFromHtml(html, gid, token, baseUrl = 'https://e-hentai.org') {
  try {
    // 构建该画廊的链接模式
    const galleryHref = `/g/${gid}/${token}/`;
    
    // 在 HTML 中找到包含该链接的块（通常是一行或一个容器）
    // 使用更大的上下文来查找 <img> 标签
    // 从 href 开始，向前和向后搜索一定的字符范围
    const hrefIndex = html.indexOf(galleryHref);
    if (hrefIndex === -1) return '';
    
    // 向前搜索最近的 <tr> 或 <div> 的开始（回溯到上一个 < 字符）
    let rowStart = hrefIndex;
    for (let i = hrefIndex; i >= 0; i--) {
      if (html[i] === '<') {
        // 检查是否是 <tr 或 <div 或其他容器
        const nextPart = html.substring(i, i + 100);
        if (nextPart.match(/^<(?:tr|div|table|section|article)/i)) {
          rowStart = i;
          break;
        }
      }
    }
    
    // 向后搜索该行的结束（</tr> 或 </div> 等）
    let rowEnd = html.indexOf('</tr>', hrefIndex);
    if (rowEnd === -1) {
      rowEnd = html.indexOf('</div>', hrefIndex);
    }
    if (rowEnd === -1) {
      rowEnd = Math.min(hrefIndex + 2000, html.length); // 默认向后搜索 2000 字符
    } else {
      rowEnd += 6; // 包含 </tr> 或 </div>
    }
    
    const rowHtml = html.substring(rowStart, rowEnd);
    
    // 查找 <img> 标签的 src 或 data-src
    const imgMatch = rowHtml.match(/<img[^>]*(?:src|data-src)=["']([^"']+)["'][^>]*>/i);
    if (imgMatch && imgMatch[1]) {
      let url = imgMatch[1];
      
      // 过滤掉 base64 数据 URI
      if (url.startsWith('data:')) {
        return '';
      }
      
      // 完善相对 URL
      if (url.startsWith('//')) {
        url = 'https:' + url;
      } else if (url.startsWith('/')) {
        url = baseUrl + url;
      }
      
      return url;
    }
    
    return '';
  } catch (e) {
    console.error('Error extracting cover URL:', e);
    return '';
  }
}
