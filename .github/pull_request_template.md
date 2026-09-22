## 📝 Description
Provide a concise explanation of the changes introduced in this PR and their business rationale.

Closes # (issue number)

---

## 🛠️ Type of Change
- [ ] 🐛 Bug fix (non-breaking change fixing an issue)
- [ ] 🚀 New feature (non-breaking change adding functionality)
- [ ] ⚡ Performance optimization
- [ ] ⚠️ Breaking change (fix or feature causing existing functionality to break)
- [ ] 🗄️ Database migration included

---

## 🧪 Verification & Testing
- [ ] `pytest` passes locally with 0 errors
- [ ] `python manage.py makemigrations --check` shows no missing migrations
- [ ] Endpoint tested with valid Authorization token and proper status codes (200/201/400)
- [ ] Concurrency/race conditions tested where applicable (e.g. bookings, payment verification)

---

## 🔒 Security & Performance Checklist
- [ ] Query optimization applied (`select_related` / `prefetch_related` checked for N+1 queries)
- [ ] Role-based permission verified (`AllowAny` vs `IsAuthenticated` vs `IsTeacher`)
- [ ] No hardcoded API keys or credentials committed
