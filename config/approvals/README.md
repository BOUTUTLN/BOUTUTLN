# Paid / unconfirmed service approvals

Services whose cost class is `paid` or `unknown` (for example KB Open API until its fee is
confirmed) are blocked. To allow one, create `paid_services.toml` here (git-ignored):

```toml
[[approved]]
provider = "kb_openapi"
approved_by = "your name"
approved_on = "2026-11-01"
reason = "Confirmed on the KB portal that the Open API fee is 0 KRW"
```

The program never creates this file by itself.
