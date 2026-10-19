# أدوات NOVA وOpenCrabs

وجهة هذا التكامل هي **novadragonno1/NOVA-DRAGON، مشروع اللعبة**. المصدر stevearagonno1/NOVA-DRAGON استُخدم لجلب الملف التنفيذي لـOpenCrabs فقط؛ لم تُنقل قواعد التداول أو بياناته أو مفاتيحه أو ذاكرته.

## التثبيت

من جذر المستودع بعد دمج التعديل:

```bash
python scripts/setup_env.py &&
.venv/bin/python scripts/setup_tools.py &&
.venv/bin/python scripts/graph_game.py &&
.venv/bin/python scripts/opencrabs_game.py prepare &&
.venv/bin/python scripts/tools_doctor.py --live
```

يُنزل المثبت نفس OpenCrabs 0.5.4 إلى `.nova/tools/opencrabs` من Commit ثابت في المصدر، ويتحقق من بصمتي الأرشيف والملف. الأرشيف يحتوي ملفًا واحدًا فقط. لا نضيف الملف التنفيذي الكبير إلى تاريخ Git؛ وصف الاستيراد والبصمات في `opencrabs-source.json`. النسخة Linux x86_64 فقط. لا يُشغّل المثبت نموذجًا أو بوت Telegram أو فحص Strix.

Ruflo مثبت في `.nova/tools/node`، وMCP وGraphify وStrix في بيئات Python منفصلة. هذا يمنع تعارض MCP 2 في الجسر مع MCP 1 في اعتماديات Strix. الإصدارات الأساسية مثبتة في `toolchain.json`؛ الاعتماديات المتعدية ليست كلها مقفلة، وملف npm lock الناتج يبقى محليًا. عند فشل تثبيت مكوّن أعد `python scripts/setup_tools.py --only اسم`.

Context7 و21st متصلان بخوادم MCP الرسمية مباشرة؛ مجلدات المصادر المستوردة القديمة ليست دليل تشغيل وليست النسخ التي ينفذها هذا الجسر.

## ما يعمل وما يحتاج إعدادًا

| المكوّن | الربط | التحقق في جلسة الإعداد |
|---|---|---|
| OpenCrabs | نفس الملف المستورد، ملف تعريف `nova-game` مستقل، Gemini، أداة `nova_tools` | نجح `--version` و`--help`؛ جلسة Gemini الحية لم تُشغّل |
| Agent Skills | قراءة محتوى المهارات عبر الجسر؛ يستطيع OpenCrabs اكتشافها واختيارها حسب المهمة | اختبارات قراءة وبصمات واختيار |
| Ruflo | MCP stdio، وظائف حالة وذاكرة ومتابعة مهام محددة | نجح الاتصال، وحفظ قيمة واسترجاعها بين عمليتين، وإنشاء سجل مهمة |
| Context7 | MCP HTTP رسمي | نجح الاتصال والبحث عن Three.js دون مفتاح في هذه الجلسة |
| Graphify | استخراج AST محلي ثم MCP للاستعلام | ملف JavaScript تجريبي: عقدتان وعلاقة واحدة؛ ثم index.html الحالي مع سكربته الداخلي: 11 عقدة و10 علاقات، وقراءة الإحصاءات عبر MCP |
| 21st | MCP HTTP رسمي؛ البحث المقروء فقط عند اكتشافه | يحتاج `API_KEY_21ST`؛ لم تُختبر مكالمة موثقة بمفتاح |
| Strix | CLI مستقل على نسخة مؤقتة من ملفات اللعبة | ثُبّت 1.7.0 ونجح `--help`؛ فحص Docker/LLM الكامل لم يُشغّل |

Ruflo أعاد 358 تعريف أداة في النسخة المختبرة؛ هذا ليس ادعاء تشغيل 358 وظيفة. المسموح ظاهر في `allowed_tools`. حفظ الذاكرة واسترجاع المفتاح نجحا دون embeddings حقيقية؛ البحث الدلالي غير مفعّل. إنشاء سجل مهمة لا يعني أن وكلاء نفّذوا برمجة، والتكامل لا يدّعي تشغيل سرب Gemini داخل Ruflo.

## الاستخدام من Telegram

البوت الحالي يبقى نقطة الدخول والكتابة. أمثلة ترسل كرسائل منفصلة:

```text
/tools
/tool ruflo list
/tool ruflo call system_status {}
/tool ruflo call memory_store {"key":"project-stage","value":"Preparing the game agent","namespace":"nova-game"}
/tool ruflo call memory_retrieve {"key":"project-stage","namespace":"nova-game"}
/tool context7 call resolve-library-id {"libraryName":"three.js","query":"InstancedMesh API documentation"}
/tool graphify call graph_stats {}
```

نتيجة آخر أداة تُضاف إلى سياق الطلب التالي للنموذج كبيانات غير موثوقة وقديمة محتملة، بحد 16 ألف حرف. `/reset` يمسحها. إيصالات الاستدعاءات الناجحة محفوظة في `.nova/tool-results/` بتاريخها؛ `/tools` لا يحول نجاحًا سابقًا إلى إثبات اتصال حاضر. فشل خدمة لا يوقف بدء البوت.

اختيار الأدوات في بوت Python الحالي ما زال عبر `/tool` واختيار المهارات عبر `/skill`. في OpenCrabs تُسجّل `nova_tools` كأداة ديناميكية في قائمة النموذج، وتوجّه تعليمات ملفه إلى اكتشاف المهارة والأداة المناسبتين تلقائيًا. هذا يتيح الاختيار للنموذج ولا يضمن جودة كل اختيار.

## OpenCrabs للعبة

```bash
.venv/bin/python scripts/opencrabs_game.py chat
```

أو مهمة واحدة:

```bash
.venv/bin/python scripts/opencrabs_game.py run "استخدم nova_tools لقراءة مهارة التخطيط ثم اقترح خطة تجهيز الوكيل دون تعديل الملفات"
```

يستخدم `GEMINI_API_KEY` و`GEMINI_MODEL` الحاليين. ملف التعريف في `~/.opencrabs/profiles/nova-game/`، منفصل عن ملف تعريف التداول. Telegram في OpenCrabs معطل حتى لا يتنافس مع بوتنا على الرسائل. لا تشغيل خلفي تلقائي، ولا تحديث ذاتي أو RSI تلقائي. التعديلات المخصصة الموجودة في ملف التعريف تُحفظ، ويكتب التجهيز بديلًا `.nova-proposed` لمراجعته بدل استبدالها.

يمر معامل الأداة عبر ملف JSON يحدده `OPENCRABS_PARAMS`، ولا يُدرج نص النموذج في أمر shell. الجسر يفرض أسماء الخوادم وقائمة الأدوات والمخططات والأحجام والمهلة. استدعاءات OpenCrabs المدمجة الأخرى تبقى تحت سياسة `ask` الخاصة به. تعليمات ترك الكتابة لبوت NOVA **ليست عزلًا على مستوى نظام التشغيل**؛ لا ندعي أن OpenCrabs صار مجبرًا على المرور بفحوص الكتابة الخاصة ببوت Python. دمج مسار كتابة واحد بالكامل يحتاج عملًا لاحقًا قبل تشغيله ككاتب مستقل.

## المفاتيح وحدود الخدمات

أسماء الإعدادات الاختيارية في `.env.example`، والقيم الحقيقية في `.env` فقط:

- `CONTEXT7_API_KEY`: اختياري لحدود أعلى؛ الوصول دون مفتاح قد يتغير أو يخضع للحصة.
- `API_KEY_21ST`: مفتاح حديث من https://21st.dev/mcp . اكتشاف القائمة لا ينشئ واجهة ولا ينشر شيئًا. الجسر لا يسمح بتوليد UI أو التثبيت أو النشر؛ يسمح فقط بأدوات بحث يعلن خادمها أنها للقراءة. إذا غابت هذه العلامات تُرفض حتى مراجعتها.
- `STRIX_LLM` و`LLM_API_KEY`: لاستخدام Strix عند طلب فحص. لا يُفترض أن الاشتراك أو الحصة مجانيان بلا حدود.

21st يذكر أن البحث مجاني، تثبيت المكونات محدود يوميًا، والتوليد يستخدم رصيدًا. لا يفعّل هذا التكامل اشتراكًا مدفوعًا. الاستخدام الخارجي يرسل نص الاستعلام إلى الخدمة المعنية؛ لا تضع مفاتيح أو معلومات خاصة في الاستعلام.

## Graphify وStrix

أعد `python scripts/graph_game.py` بعد تغير الكود. لا يمسح المستودع كله: ينسخ ملفات كود اللعبة فقط ضمن حدود الحجم إلى مجلد مؤقت، ثم يستخرج محليًا بلا مفاتيح أو LLM. يستخرج JavaScript الداخلي من HTML لأن Graphify يصنف HTML كوثيقة ويتجاهله مع code-only. أسماء المسارات في الرسم تعود إلى النسخة المؤقتة؛ يربطها source-map.json بملفات اللعبة وعناصر script الأصلية. لا تعتبر المسارات المؤقتة موجودة بعد انتهاء الفهرسة. يعرض الرسم علاقات مستخرجة ولا يثبت صحة اللعبة.

فحص Strix مستقل ومقصور على نسخة من `game_engine`:

```bash
.venv/bin/python scripts/scan_game.py --run
```

يحتاج Docker يعمل، ومفتاح النموذج وإعداده. يفحص نسخة مؤقتة قابلة للتعديل، لا ملفات العمل الأصلية. وضع quick وحد 20 دورة لكل وكيل يقللان العمل لكنهما لا يحددان التكلفة الكلية بالدولار. أمر `--run` هو بدء الفحص الفعلي ويستهلك حصة النموذج؛ لا ينفذه التجهيز أو الوكيل تلقائيًا. التقارير في `.nova/strix/strix_runs/`. لم نختبر فحصًا حيًا ولم نفحص خدمة خارجية.

## النقل

تُعاد الأدوات من وصف التثبيت. تصدير NOVA السابق ينقل محادثته وسجل مهامه فقط؛ لا ينقل تلقائيًا ذاكرة Ruflo في `.nova/tool-work/ruflo`، أو ملفات تعريف OpenCrabs تحت home، أو تقارير الأدوات. احتفظ بنسخة خاصة من هذه المسارات والبوتات متوقفة قبل حذف البيئة. لا ترفعها إلى GitHub.

## المصادر

- https://github.com/opencrabs/opencrabs (الإعدادات والأدوات الديناميكية)
- https://github.com/addyosmani/agent-skills
- https://github.com/upstash/context7
- https://21st.dev/mcp وhttps://github.com/21st-dev/magic-mcp
- https://github.com/usestrix/strix
- https://github.com/ruvnet/ruflo
- https://github.com/Graphify-Labs/graphify
- https://github.com/modelcontextprotocol/python-sdk
