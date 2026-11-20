"""Sample CVs shared by the career-profile tests."""

from __future__ import annotations

import pytest

ENGLISH_CV = """Ada Lovelace
Senior Software Engineer
ada.lovelace@example.com | +966 55 123 4567 | linkedin.com/in/ada-lovelace
Riyadh, Saudi Arabia

PROFESSIONAL SUMMARY
Backend engineer with eight years of experience building payment and data platforms.

Work Experience
Senior Software Engineer, Acme Payments Inc.        Mar 2021 – Present
• Led the migration of the ledger to PostgreSQL
• Mentored four engineers

Software Engineer
Globex Corporation | 01/2017 - 02/2021
- Built the reporting API in Python

Education
Bachelor of Science in Computer Science
King Saud University, 2012 - 2016

Skills
Languages: Python, Go, SQL
Tools: Docker, Kubernetes; Terraform

Certifications
AWS Certified Solutions Architect – Associate (2022)
Certified Kubernetes Administrator 2023

Languages
Arabic (Native), English - Fluent
"""

ARABIC_CV = """سارة أحمد
sara@example.com
+966501112233

الملخص المهني
محللة بيانات لديها خبرة خمس سنوات في تحليل البيانات المالية وإعداد التقارير.

الخبرات العملية
محلل بيانات في شركة الاتصالات السعودية
يناير 2020 - حتى الآن
- إعداد لوحات المعلومات الأسبوعية

التعليم
بكالوريوس نظم المعلومات
جامعة الملك عبدالعزيز 2015 - 2019

المهارات
بايثون، SQL، Power BI

اللغات
العربية: اللغة الأم، الإنجليزية: متقدم
"""


@pytest.fixture(scope="session")
def english_cv() -> str:
    return ENGLISH_CV


@pytest.fixture(scope="session")
def arabic_cv() -> str:
    return ARABIC_CV
