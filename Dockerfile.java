FROM maven:3.9-eclipse-temurin-21 AS compilacao
WORKDIR /codigo
COPY pom.xml ./
COPY src ./src
RUN mvn --batch-mode --no-transfer-progress package -DskipTests

FROM eclipse-temurin:21-jre-noble
WORKDIR /app
RUN mkdir -p /estado && chown 10001:10001 /estado
COPY --from=compilacao /codigo/target/*-jar-with-dependencies.jar /app/streamguard.jar
USER 10001:10001
ENTRYPOINT ["java", "-Xmx512m", "-jar", "/app/streamguard.jar"]
