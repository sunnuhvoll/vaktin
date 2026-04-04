---
layout: default
title: Vikuskýrslur
---

# Vikuskýrslur

[← Til baka](../)

{% assign reports = site.pages | where_exp: "page", "page.dir == '/weekly/'" | where_exp: "page", "page.name != 'index.md'" | sort: "name" | reverse %}

{% if reports.size > 0 %}
{% for report in reports %}
- [{{ report.title | default: report.name | remove: '.md' }}]({{ report.url | relative_url }})
{% endfor %}
{% else %}
*Engar vikuskýrslur enn.*
{% endif %}

---
*Sjálfvirk skýrsla frá [Vaktin](https://github.com/INECTA/vaktin)*
