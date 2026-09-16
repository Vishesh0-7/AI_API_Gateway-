FROM node:20-slim

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

ENV NEXT_PUBLIC_GATEWAY_URL=http://localhost:8080

EXPOSE 3000
CMD ["npm", "run", "dev"]
