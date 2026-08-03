# GEO 第一阶段 - 需要你手工完成的部署项

以下事项无法通过静态 HTML 文件完成，需要你在服务器 / DNS / 站长平台上手动操作。

## 1. HTTP 强制跳转 HTTPS

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

目前 `corner-stone.cn` 和 `www.corner-stone.cn` 都无法解析（curl 返回连接失败），
只有 `home.corner-stone.cn` 这一个可访问入口。建议：

- 在域名 DNS 里为 `corner-stone.cn` 和 `www.corner-stone.cn` 各加一条解析记录；
- 让这两个域名 301 跳转到 `https://home.corner-stone.cn/`（同一份内容不要放两份）。

这一步不是必须的，但能避免有人搜到裸域名却打不开，也能把所有品牌相关的域名权重集中到一个地址上。

## 3. 提交站点到搜索引擎 / 站长平台

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
- `knowledge.html` 里有两条内容存在疑似标题/标签错误（详见页面里的 `TODO(GEO)` 注释），
  需要你对照微信公众号原文核实真实标题后再修正，我不确定原文内容所以没有替你编。
