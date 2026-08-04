# GEO 第一阶段 - 需要你手工完成的部署项

以下事项无法通过静态 HTML 文件完成，需要你在服务器 / DNS / 站长平台上手动操作。

## 1. HTTP 强制跳转 HTTPS 
(已完成)

当前 `http://home.corner-stone.cn/` 和 `https://home.corner-stone.cn/` 都能返回 200，
等于同一份内容存在两个地址，会被搜索引擎/AI 判定为重复内容。

如果你用 Nginx，在 80 端口的 server block 里加一条即可：

```nginx
server {
    listen 80;
    server_name home.corner-stone.cn;
    return 301 https://$host$request_uri;
}
```

如果是用宝塔 / 云厂商控制台管理，通常有"强制 HTTPS"的开关，勾选即可，不需要手写配置。

## 2. 根域名 / www 子域名
(暂时不做了)

目前 `corner-stone.cn` 和 `www.corner-stone.cn` 都无法解析（curl 返回连接失败），
只有 `home.corner-stone.cn` 这一个可访问入口。建议：

- 在域名 DNS 里为 `corner-stone.cn` 和 `www.corner-stone.cn` 各加一条解析记录；
- 让这两个域名 301 跳转到 `https://home.corner-stone.cn/`（同一份内容不要放两份）。

这一步不是必须的，但能避免有人搜到裸域名却打不开，也能把所有品牌相关的域名权重集中到一个地址上。

## 3. 提交站点到搜索引擎 / 站长平台
(等完善了所有网页后再做)

代码里已经新增了 `robots.txt`、`sitemap.xml`、`llms.txt`，部署上线后建议做：

- 百度搜索资源平台（zhanzhang.baidu.com）：提交站点 + 提交 `sitemap.xml`
- 搜狗/360/神马 站长平台：同上
- Google Search Console / Bing Webmaster Tools：提交站点 + `sitemap.xml`（如果你的目标用户也含海外）

DeepSeek、豆包、通义千问目前没有公开的"站长提交入口"或专属爬虫 UA 声明，
它们对网页内容的获取主要依赖自身的搜索/索引链路（部分复用百度、搜狗等公开搜索结果），
所以现阶段能做的就是：把内容做好、把 `robots.txt` 放开、把站点提交给上述国内搜索引擎收录，
剩下的只能靠后续定期人工测试提问来验证是否被引用（见对话中提到的"第四阶段"）。

## 4. llms.txt / JSON-LD 里的实体信息

`llms.txt`、`index.html` / `about.html` 头部的 JSON-LD、以及各页面二维码旁的文字，
已根据你在 `第一阶段信息补充.md` 里提供的信息（成立年份、地址、电话、邮箱、公众号全名、
微信号、统一社会信用代码、ICP备案号）统一填入并保持口径一致。

仍然没有解决的两个遗留问题：

- `og:image` 分享卡片图：目前没有可用的 1200x630 品牌图片，`index.html` 里相关标签仍被注释掉，
  需要设计一张图后再启用。

## 5. 学员录取结果总表（`data/admissions-records.html`）的可见性策略

这是 GEO 第二阶段新增的一页，由 `build_admissions_table.py` 读取 `all_cases.csv`（全量历年录取记录，
共约 197 条，远多于「成功案例」栏目里已发布完整战报的 ~25 篇）清洗生成。按你的要求，这份数据要做到
「大模型能读，普通用户读不到，但技术型用户主动爬取没关系」，具体实现方式：

- 页面本身没有做任何"对人和对 AI 显示不同内容"的伪装（cloaking 会伤害整站可信度，不采用）；
  控制的是"能不能被发现"，不是"内容本身"。
- 站内任何导航、`cases.html` 列表、`index.html` 首页都**没有链接**指向这一页。
- **没有**放进 `sitemap.xml`（`build_geo_pages.py` 重新生成 sitemap 时也不会包含它）。
- 页面 `<meta name="robots" content="noindex, noarchive">`，且 `robots.txt` 里对 Googlebot / Bingbot /
  百度 / 搜狗 / 360 / 神马 / Bytespider 等**常规搜索引擎爬虫**单独 `Disallow: /data/`，
  确保它不会出现在任何人的搜索结果里。
- `robots.txt` 里**没有**限制 GPTBot / ClaudeBot / PerplexityBot / Google-Extended 等 AI 抓取器，
  也没有限制默认的 `User-agent: *`（即未声明身份的通用抓取脚本/命令行工具），
  所以 AI 助手抓取引用、以及你说的"能写指令读取这个域名下所有信息"的技术型用户，都不受影响。
- `llms.txt` 里显式列出了这个页面的 URL，专门告知 AI 助手它的存在和用途。

如果之后 `all_cases.csv` 有更新，重新运行 `python build_admissions_table.py` 即可增量重新生成这一页，
不需要手动改 `robots.txt` / `llms.txt`（这两处的路径级规则是一次性写好的，不随数据变化）。
