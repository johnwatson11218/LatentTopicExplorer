# node-app/Dockerfile
FROM node:22-alpine
WORKDIR /usr/src/app
COPY package*.json ./
RUN npm ci 
COPY . .
EXPOSE 3000
#CMD ["node", "app.js"]

