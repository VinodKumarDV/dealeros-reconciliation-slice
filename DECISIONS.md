# Decisions

1. **Used PostgreSQL Row-Level Security for the tenant isolation.** Instead of relying only on ORM filters, I have used database-level policies so that even queries without organization filters cannot access another tenant's data.

2. **Used transaction-local organization setting.** I avoided session-level settings because database connections may be reused across requests. Transaction-local settings are automatically reset after a commit or rollback, reducing the risk of tenant data leaking between requests.

3. **Used System A location when locations don't match.** When the two systems report different locations, I treat System A's location as the reference for assigning the exception to an organization. This avoids accidentally exposing another organization's records because of a conflicting System B location.

4. **Allowed a two-day difference in dates.** I treated date differences of up to two days as normal posting delays instead of flagging them as errors. This is based on the supplied data, where a two-day delay appears to be intentional. I kept this as a limited business rule rather than ignoring all date mismatches.

5. **Handled multiple System B entries based on their amounts.** I didn't treat every one-to-many match as a duplicate. If multiple entries correctly add up to the System A amount, they are accepted as a valid split. For example, REC-1055 is an intentional split, while REC-1042 contains a duplicate entry.

6. **Kept the question-answering endpoint deterministic.** Rather than using an LLM to generate answers, I used predefined query logic for counts, totals and record lookups. This makes it easier to trace answers back to source records and refuse questions when the available data is insufficient.

7. **Kept the UI simple and used hardcoded demo users.** I chose a plain React table and server-side sessions instead of spending time building a production authentication system. Each session is linked to a predefined user and organization on the server, so the client cannot choose which organization's data to access.