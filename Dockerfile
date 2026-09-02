# Stage 1: Build the React frontend
FROM node:18-alpine AS frontend-builder
WORKDIR /frontend

# Install dependencies
COPY frontend/package*.json ./
RUN npm install

# Copy source and build
COPY frontend/ ./
RUN npm run build


# Stage 2: Build the FastAPI backend and serve both
FROM python:3.11-slim
WORKDIR /app

# Install backend dependencies
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend code
COPY backend/ .

# Copy built frontend from Stage 1 to a location the backend expects
COPY --from=frontend-builder /frontend/dist /app/frontend/dist

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
