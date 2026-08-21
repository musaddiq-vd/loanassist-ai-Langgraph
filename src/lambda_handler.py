from mangum import Mangum

from src.app.main import app

# API Gateway Lambda integration entrypoint: src.lambda_handler.handler
handler = Mangum(app, lifespan="auto")
