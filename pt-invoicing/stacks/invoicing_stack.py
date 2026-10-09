import json

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_authorizers as authorizers,
    aws_apigatewayv2_integrations as integrations,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_dynamodb as dynamodb,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_lambda_event_sources as sources,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_ses as ses,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_sqs as sqs,
)
from constructs import Construct


class InvoicingStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, config: dict, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        biz, brand, domain = config["business"], config["brand"], config.get("domain", {})
        app_config = json.dumps({"business": biz, "brand": brand})
        custom_domain = bool(domain.get("name") and domain.get("certificate_arn"))

        # ---------------------------------------------------------------- data
        table = dynamodb.Table(
            self, "Table",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        files_bucket = s3.Bucket(
            self, "Files",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ------------------------------------------------------------ front end
        site_bucket = s3.Bucket(
            self, "Site",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        dist_extra = {}
        if custom_domain:
            dist_extra = {
                "domain_names": [domain["name"]],
                "certificate": acm.Certificate.from_certificate_arn(self, "Cert", domain["certificate_arn"]),
            }
        dist = cloudfront.Distribution(
            self, "Dist",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
            ),
            **dist_extra,
        )
        site_url = f"https://{domain['name']}" if custom_domain else f"https://{dist.distribution_domain_name}"

        # ----------------------------------------------------------------- auth
        pool = cognito.UserPool(
            self, "Pool",
            user_pool_name="pt-invoicing",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            mfa=cognito.Mfa.REQUIRED,
            mfa_second_factor=cognito.MfaSecondFactor(otp=True, sms=False),
            password_policy=cognito.PasswordPolicy(min_length=12),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            removal_policy=RemovalPolicy.RETAIN,
        )
        client = pool.add_client(
            "Web",
            generate_secret=False,
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(authorization_code_grant=True),
                scopes=[cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL],
                callback_urls=[f"{site_url}/"],
                logout_urls=[f"{site_url}/"],
            ),
            supported_identity_providers=[cognito.UserPoolClientIdentityProvider.COGNITO],
            id_token_validity=Duration.hours(1),
            access_token_validity=Duration.hours(1),
            refresh_token_validity=Duration.days(90),
            prevent_user_existence_errors=True,
        )
        pool_domain = pool.add_domain(
            "Domain",
            cognito_domain=cognito.CognitoDomainOptions(domain_prefix=f"pt-invoices-{self.account}"),
        )

        # ------------------------------------------------------- event plumbing
        bus = events.EventBus(self, "Bus", event_bus_name="pt-events")
        dlq = sqs.Queue(self, "EmailDlq", retention_period=Duration.days(14))
        queue = sqs.Queue(
            self, "EmailQueue",
            visibility_timeout=Duration.seconds(360),
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq),
        )
        events.Rule(
            self, "EmailRequested",
            event_bus=bus,
            event_pattern=events.EventPattern(detail_type=["email.requested"]),
            targets=[targets.SqsQueue(queue, message=events.RuleTargetInput.from_event_path("$.detail"))],
        )

        # SES bounce/complaint feedback -> SNS -> mailer
        feedback_topic = sns.Topic(self, "SesFeedback")
        feedback_topic.add_to_resource_policy(iam.PolicyStatement(
            principals=[iam.ServicePrincipal("ses.amazonaws.com")],
            actions=["sns:Publish"],
            resources=[feedback_topic.topic_arn],
            conditions={"StringEquals": {"AWS:SourceAccount": self.account}},
        ))
        config_set = ses.ConfigurationSet(self, "SesConfigSet", configuration_set_name="pt-mailer")
        config_set.add_event_destination(
            "Feedback",
            destination=ses.EventDestination.sns_topic(feedback_topic),
            events=[ses.EmailSendingEvent.BOUNCE, ses.EmailSendingEvent.COMPLAINT],
        )

        # ------------------------------------------------------------- lambdas
        mailer = _lambda.Function(
            self, "Mailer",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("lambdas/mailer"),
            timeout=Duration.seconds(60),
            memory_size=512,
            log_group=logs.LogGroup(self, "MailerLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
            environment={
                "APP_CONFIG": app_config,
                "TABLE": table.table_name,
                "FILES_BUCKET": files_bucket.bucket_name,
                "SES_CONFIG_SET": config_set.configuration_set_name,
            },
        )
        table.grant_read_write_data(mailer)
        files_bucket.grant_put(mailer)
        mailer.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=[
                f"arn:aws:ses:{self.region}:{self.account}:identity/*",
                f"arn:aws:ses:{self.region}:{self.account}:configuration-set/{config_set.configuration_set_name}",
            ],
        ))
        mailer.add_event_source(sources.SqsEventSource(queue, batch_size=5, report_batch_item_failures=True))
        feedback_topic.add_subscription(subs.LambdaSubscription(mailer))

        api_fn = _lambda.Function(
            self, "Api",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=_lambda.Code.from_asset("lambdas/api"),
            timeout=Duration.seconds(15),
            memory_size=256,
            log_group=logs.LogGroup(self, "ApiLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
            environment={
                "APP_CONFIG": app_config,
                "TABLE": table.table_name,
                "BUS": bus.event_bus_name,
                "FILES_BUCKET": files_bucket.bucket_name,
            },
        )
        table.grant_read_write_data(api_fn)
        bus.grant_put_events_to(api_fn)
        files_bucket.grant_read(api_fn)

        # ------------------------------------------------------------------ API
        http_api = apigw.HttpApi(
            self, "HttpApi",
            api_name="pt-invoicing",
            create_default_stage=False,
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=[site_url],
                allow_methods=[apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.PUT],
                allow_headers=["authorization", "content-type"],
                max_age=Duration.hours(1),
            ),
        )
        stage = apigw.HttpStage(
            self, "Stage",
            http_api=http_api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=10, burst_limit=20),
        )
        authorizer = authorizers.HttpJwtAuthorizer(
            "Jwt",
            jwt_issuer=f"https://cognito-idp.{self.region}.amazonaws.com/{pool.user_pool_id}",
            jwt_audience=[client.user_pool_client_id],
        )
        integration = integrations.HttpLambdaIntegration("ApiIntegration", api_fn)
        M = apigw.HttpMethod
        for path, methods in {
            "/customers": [M.GET, M.POST],
            "/customers/{id}": [M.PUT],
            "/invoices": [M.GET, M.POST],
            "/invoices/{number}/resend": [M.POST],
            "/invoices/{number}/status": [M.POST],
            "/invoices/{number}/pdf": [M.GET],
        }.items():
            http_api.add_routes(path=path, methods=methods, integration=integration, authorizer=authorizer)

        api_url = f"https://{http_api.api_id}.execute-api.{self.region}.amazonaws.com"

        # ------------------------------------------------------ deploy the site
        s3deploy.BucketDeployment(
            self, "DeploySite",
            sources=[
                s3deploy.Source.asset("frontend"),
                s3deploy.Source.json_data("config.json", {
                    "apiUrl": api_url,
                    "cognitoDomain": pool_domain.base_url().replace("https://", ""),
                    "clientId": client.user_pool_client_id,
                    "redirectUri": f"{site_url}/",
                    "brand": brand,
                    "businessName": biz["name"],
                }),
            ],
            destination_bucket=site_bucket,
            distribution=dist,
            distribution_paths=["/*"],
        )

        # -------------------------------------------------------------- outputs
        CfnOutput(self, "SiteUrl", value=site_url)
        CfnOutput(self, "CloudFrontDomain", value=dist.distribution_domain_name,
                  description="Point your CNAME here if you use a custom domain")
        CfnOutput(self, "UserPoolId", value=pool.user_pool_id)
        CfnOutput(self, "ApiUrl", value=api_url)
