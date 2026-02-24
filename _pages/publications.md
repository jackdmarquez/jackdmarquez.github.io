---
layout: archive
title: "Publications"
permalink: /publications/
author_profile: true
---

{% if author.googlescholar %}
  You can also find my articles on <u><a href="{{author.googlescholar}}">my Google Scholar profile</a>.</u>
{% endif %}

{% include base_path %}

{% assign current_year = "" %}
{% for post in site.publications reversed %}
  {% assign post_year = post.date | date: "%Y" %}
  {% if post_year != current_year %}
    {% assign current_year = post_year %}
<h2 class="archive__subtitle">{{ current_year }}</h2>
  {% endif %}
  {% include archive-single.html %}
{% endfor %}
