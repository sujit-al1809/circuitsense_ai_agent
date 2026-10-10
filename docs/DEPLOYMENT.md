# Deploying CircuitSense on AWS

> **Status:** this guide describes a deployment path. Nothing has been deployed from this repository, and the Docker image was not built on the development machine (Docker was not installed there). The single-container serving mode it relies on (`FRONTEND_DIST`) *was* verified locally, and a test covers it.

## Recommended path: container on Amazon ECS (AWS Fargate) behind an ALB

```
Browser ──HTTPS──▶ Application Load Balancer ──▶ ECS Fargate task (1 replica)
                                                   └─ CircuitSense container
                                                        ├─ FastAPI API + built React UI
                                                        ├─ Amazon Bedrock (Converse)  ← task IAM role
                                                        └─ Amazon S3 (optional upload archive)
```

**Why a container rather than AWS Lambda for this MVP:**

- **Uploads:** synchronous Lambda invocations are limited to a 6 MB request payload, below the default 10 MB upload limit.
- **Sessions:** analysis sessions live in process memory, and Lambda execution environments are not sticky, so a follow-up request can land in an instance that never saw the session.
- **Long requests:** a single chat turn can make several Bedrock calls (up to `AGENT_MAX_ITERATIONS`).

Lambda becomes reasonable once sessions move to a persistent `SessionStore` (for example DynamoDB, with uploads in S3) and `MAX_UPLOAD_MB` is lowered or uploads go straight to S3 through pre-signed URLs.

Run **one task**: sessions are in memory (see README → Limitations).

---

## 1. Prerequisites

- AWS CLI v2, Docker, and an AWS account/region where Amazon Bedrock is available.
- **Bedrock model access** enabled in the console (*Bedrock → Model access*) for:
  - a text model that supports **tool use via the Converse API**, and
  - a model that accepts **IMAGE** input (it can be the same model).
- The exact model or **inference-profile** IDs your account can invoke. Many newer models are only invocable through cross-region inference profiles:

```bash
export AWS_REGION=us-east-1
aws bedrock list-foundation-models --region $AWS_REGION \
  --query "modelSummaries[].[modelId,join(',',inputModalities),join(',',inferenceTypesSupported)]" --output table
aws bedrock list-inference-profiles --region $AWS_REGION \
  --query "inferenceProfileSummaries[].[inferenceProfileId,status]" --output table
```

Validate locally before deploying: set `PROVIDER_MODE=bedrock` with these IDs in `backend/.env`, then call `GET /api/provider/status?probe=true`.

## 2. Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `PROVIDER_MODE` | yes | `bedrock` in AWS (or `demo` for a credential-free showcase) |
| `AWS_REGION` | bedrock | Bedrock region |
| `BEDROCK_TEXT_MODEL_ID` | bedrock | Model / inference-profile ID with Converse tool use |
| `BEDROCK_VISION_MODEL_ID` | recommended | Model / inference-profile ID with IMAGE input (defaults to the text model) |
| `BEDROCK_MAX_TOKENS` | no | Per-call output cap (default 4096) |
| `AGENT_MAX_ITERATIONS`, `AGENT_MAX_TOOL_CALLS_PER_ITERATION`, `AGENT_MAX_TOOL_FAILURES` | no | Agent loop bounds (6 / 4 / 3) |
| `MAX_UPLOAD_MB`, `MAX_PDF_PAGES`, `SESSION_MAX_COUNT` | no | Input and memory limits |
| `S3_BUCKET`, `S3_PREFIX` | no | Archive original uploads to S3 |
| `NGSPICE_PATH` | no | Build with `--build-arg INSTALL_NGSPICE=true` to include ngspice |
| `FRONTEND_DIST`, `DATA_DIR` | set in image | Built UI and sample data locations |
| `CORS_ORIGINS` | no | Only needed if the UI is hosted on a different origin |

No credentials go into environment variables: the container uses the **ECS task role**.

## 3. Build and push the image (Amazon ECR)

```bash
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REPO=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/circuitsense
export TAG=$(date +%Y%m%d%H%M)

aws ecr create-repository --repository-name circuitsense --image-scanning-configuration scanOnPush=true --region $AWS_REGION
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -t circuitsense:$TAG .                      # add --build-arg INSTALL_NGSPICE=true for simulation
docker run --rm -p 8000:8000 circuitsense:$TAG          # local check: http://localhost:8000 (demo mode)
docker tag circuitsense:$TAG $REPO:$TAG && docker push $REPO:$TAG
```

## 4. IAM roles (least privilege)

**Execution role** (pulls the image, writes logs): trust `ecs-tasks.amazonaws.com` and attach the AWS managed policy `AmazonECSTaskExecutionRolePolicy`.

**Task role** (what the app itself may do): trust `ecs-tasks.amazonaws.com` and attach [`deploy/iam-task-policy.json`](../deploy/iam-task-policy.json) after replacing the placeholders. The policy allows:

- `bedrock:InvokeModel` (used by the Converse API) **only** on the configured inference profiles and their underlying foundation models. Cross-region profiles route to several regions, which is why the foundation-model ARNs use `*` for the region.
- `bedrock:GetFoundationModel` / `bedrock:GetInferenceProfile` for `/api/provider/status`.
- `s3:PutObject` only under `circuitsense/uploads/` in your bucket, and only if you enable the archive.

```bash
cat > trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
aws iam create-role --role-name circuitsense-execution-role --assume-role-policy-document file://trust.json
aws iam attach-role-policy --role-name circuitsense-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam create-role --role-name circuitsense-task-role --assume-role-policy-document file://trust.json
aws iam put-role-policy --role-name circuitsense-task-role --policy-name circuitsense-app \
  --policy-document file://deploy/iam-task-policy.json      # after editing placeholders
```

## 5. Optional: S3 upload archive

```bash
export BUCKET=circuitsense-uploads-$ACCOUNT_ID
aws s3api create-bucket --bucket $BUCKET --region $AWS_REGION \
  $( [ "$AWS_REGION" != "us-east-1" ] && echo --create-bucket-configuration LocationConstraint=$AWS_REGION )
aws s3api put-public-access-block --bucket $BUCKET \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-uploads","Status":"Enabled","Filter":{"Prefix":"circuitsense/uploads/"},"Expiration":{"Days":30}}]}'
```

Set `S3_BUCKET=$BUCKET` in the task definition. Objects are written with SSE-S3 (`AES256`). Archive failures are logged and never break an analysis.

## 6. ECS cluster, task definition and service

```bash
aws logs create-log-group --log-group-name /ecs/circuitsense --region $AWS_REGION
aws ecs create-cluster --cluster-name circuitsense --region $AWS_REGION
# Edit deploy/ecs-task-definition.json: ACCOUNT_ID, REGION, IMAGE_TAG, model IDs, S3_BUCKET
aws ecs register-task-definition --cli-input-json file://deploy/ecs-task-definition.json --region $AWS_REGION
```

Networking (use your VPC's subnets and security groups):

1. **ALB security group:** allow inbound 443 (and 80 for a redirect) from your users.
2. **Task security group:** allow inbound 8000 **only from the ALB security group**.
3. **Target group:** type `ip`, protocol HTTP, port 8000, health check path `/health`.
4. **ALB:** an HTTPS listener with an ACM certificate forwarding to the target group. Set the ALB idle timeout to about 180 s, because a multi-step agent turn can take longer than the 60 s default.

```bash
aws elbv2 create-target-group --name circuitsense-tg --protocol HTTP --port 8000 --target-type ip \
  --vpc-id <vpc-id> --health-check-path /health --region $AWS_REGION
# create-load-balancer / create-listener with your subnets, security group and ACM certificate, then:
aws elbv2 modify-load-balancer-attributes --load-balancer-arn <alb-arn> \
  --attributes Key=idle_timeout.timeout_seconds,Value=180

aws ecs create-service --cluster circuitsense --service-name circuitsense \
  --task-definition circuitsense --desired-count 1 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<private-subnet-a>,<private-subnet-b>],securityGroups=[<task-sg>],assignPublicIp=DISABLED}" \
  --load-balancers "targetGroupArn=<tg-arn>,containerName=circuitsense,containerPort=8000" \
  --region $AWS_REGION
```

Tasks in private subnets need a route to ECR, CloudWatch Logs, Bedrock and S3, either through a NAT gateway or through VPC endpoints (`ecr.api`, `ecr.dkr`, `logs`, `bedrock-runtime`, `bedrock`, and an S3 gateway endpoint).

**Authentication:** CircuitSense has none built in. Before sharing the URL, add ALB authentication (Amazon Cognito or OIDC) or restrict the ALB security group.

## 7. Frontend / backend configuration

- **Default (single origin):** the image serves the built UI from the API (`FRONTEND_DIST`), so the browser calls `/api/...` on the same host. No CORS setting is needed.
- **Separate static hosting (optional):** build with `VITE_API_BASE_URL=https://api.example.com npm run build`, host `frontend/dist` on S3 + CloudFront, and set `CORS_ORIGINS=https://app.example.com` on the API.

## 8. Smoke tests

```bash
export URL=https://<your-alb-dns-or-domain>
curl -s $URL/health                                     # "status":"ok", provider.mode "bedrock", configured true
curl -s "$URL/api/provider/status?probe=true"           # both checks ok:true; probe costs a few tokens
CID=$(curl -s -X POST $URL/api/samples/led_basic/load | python -c "import sys,json; print(json.load(sys.stdin)['analysis']['circuit_id'])")
curl -s -X POST $URL/api/chat -H "Content-Type: application/json" \
  -d "{\"circuit_id\":\"$CID\",\"message\":\"Calculate current through the LED.\"}" | python -m json.tool | head -40
# Expect "mode": "bedrock", a calculate_electrical_values tool call and 9.09 mA in the answer.
curl -s -F "file=@data/sample_circuits/led_basic/schematic.png" $URL/api/circuits/analyze | python -m json.tool | head -30
# In bedrock mode this runs the vision model: components/nets carry image_inference / inferred.
```

Then open `$URL` in a browser, load a reference circuit, select a component and ask a question.

## 9. Cost considerations

Check current prices on the AWS pricing pages for your region. The cost drivers are:

- **Amazon Bedrock (usually dominant):** billed per input and output token, and the price varies widely by model. Each chat turn makes 1 to `AGENT_MAX_ITERATIONS` Converse calls, and each call resends the system prompt, circuit context, history and tool results. Every image analysis sends one image (image tokens scale with resolution; the upload is downscaled to `MODEL_IMAGE_MAX_DIMENSION`, 1568 px by default). To control cost: lower `AGENT_MAX_ITERATIONS` and `AGENT_HISTORY_TURNS`, cap `BEDROCK_MAX_TOKENS`, choose a smaller model for the text role, and use `probe=true` sparingly.
- **AWS Fargate:** vCPU-hours plus GB-hours for one 0.5 vCPU / 1 GB task running continuously.
- **Application Load Balancer:** hourly charge plus LCUs.
- **NAT gateway** (if used): hourly charge plus per-GB processing. VPC endpoints are an alternative with their own hourly charges.
- **Amazon ECR, CloudWatch Logs, Amazon S3:** small at MVP scale. Use the lifecycle rule above, and set a log retention period.

Create an **AWS Budgets** alert before inviting users, and consider ALB authentication or rate limiting to stop anonymous traffic from running up Bedrock usage.

## 10. Cleanup

```bash
aws ecs update-service --cluster circuitsense --service circuitsense --desired-count 0 --region $AWS_REGION
aws ecs delete-service --cluster circuitsense --service circuitsense --force --region $AWS_REGION
aws ecs delete-cluster --cluster circuitsense --region $AWS_REGION
aws ecs deregister-task-definition --task-definition circuitsense:<revision> --region $AWS_REGION
aws elbv2 delete-load-balancer --load-balancer-arn <alb-arn> --region $AWS_REGION
aws elbv2 delete-target-group --target-group-arn <tg-arn> --region $AWS_REGION
aws ecr delete-repository --repository-name circuitsense --force --region $AWS_REGION
aws logs delete-log-group --log-group-name /ecs/circuitsense --region $AWS_REGION
aws s3 rm s3://$BUCKET --recursive && aws s3api delete-bucket --bucket $BUCKET --region $AWS_REGION   # if created
aws iam delete-role-policy --role-name circuitsense-task-role --policy-name circuitsense-app
aws iam delete-role --role-name circuitsense-task-role
aws iam detach-role-policy --role-name circuitsense-execution-role --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam delete-role --role-name circuitsense-execution-role
# Also delete any NAT gateway / VPC endpoints / ACM certificate created only for this deployment.
```
