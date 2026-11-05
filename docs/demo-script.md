# Demo video script (2 to 5 minutes)

The assignment asks for five things, in this order. Timings add up to about 4:30. Speak to someone who doesn't know AWS: say what each thing does before you name it. Everything in *italics* has to be your own real experience, so fill it in after you deploy.

**Before recording:** have these open in tabs: the live site, `docs/architecture.png`, the GitHub Actions page, the AWS Billing page. Run the seed reset so that the data looks clean. Do one full practice run.

## 1. The app working live (45 s)
- Open the live URL. "This is Roomly, a meeting-room booking app that several companies share."
- Click **Acme: employee**. Book a free slot and show it appear in the grid.
- Point at the footer: "This line tells you which server served the page and which version of the code. You'll see why that matters."
- Open `/health`: "This is the health check. It shows the app is up and that it can reach the database, with real data read back."
- In a terminal, run the race:

```bash
python scripts/race.py --base-url https://<your-site> --org acme --n 50 --cleanup
```

- "50 people try to book the same room at the same moment. Exactly one wins, and that's enforced by the database, not by my code hoping for the best."

## 2. Architecture and choices (90 s)
Show `docs/architecture.png` and follow the numbered arrows: browser → public IP → Caddy for HTTPS → app → database in a private network → back again.

For each choice, say what you picked, one alternative, and why not:

| Choice | Picked | Considered | Why not |
|--------|--------|-----------|---------|
| Server size | t4g.micro | t3.micro | Graviton has better price-performance, and either fits this load |
| Linux | Amazon Linux 2023 | Ubuntu | AL2023 comes with the AWS tooling I rely on, since there's no SSH to fix things by hand |
| Database | RDS Postgres | DynamoDB | I rely on Postgres features to block double bookings and separate customers. In DynamoDB they'd be application code |
| Traffic | Caddy + Elastic IP | Load balancer | A load balancer costs about $16 a month to sit in front of one server |
| Recovery | Auto Scaling Group of 1 | One plain instance | If the server dies, a new one appears at the same address |

Tradeoff to name out loud: "This is one server, so replacing it means a few minutes of downtime. I accepted that for the Free Tier."

## 3. Security and cost (45 s)
- "There's no SSH port. Only 80 and 443 are open, and the database only accepts connections from the app server." Show the security group.
- "There are no passwords in the repo. They're generated and kept in AWS Parameter Store, and GitHub deploys with short-lived credentials, so there are no AWS keys in GitHub."
- Cross-tenant demo: show `isolation_demo.sh` returning 404. "This route has no tenant check in Python on purpose. The database hides the row."
- Cost: show the Billing page and the budget alerts. "No NAT Gateway, no load balancer. The one thing to watch is the public IP, billed hourly outside the free allowance." *Say what your bill actually is.*

## 4. Something that went wrong (45 s)
*Tell the real story from `docs/POSTMORTEM.md`.* Structure: what you saw → your first guess and why it was wrong → what you checked next → the real cause → the fix → what stops it from recurring. The wrong guess is the most convincing part, so keep it in.

## 5. Before real customers (30 s)
From `docs/production-readiness.md`: "Three things first. A load balancer with two servers across data centers, because one server is my biggest risk. A Multi-AZ database with tested restores, because losing data is the one thing I can't undo. And a staging environment, because right now production is the first real test."

## Optional wow moments, if time allows
- Terminate the instance live, and cut to the site back at the same URL with a new id in the footer.
- Push a broken build and show the Deploy job rolling back by itself.
