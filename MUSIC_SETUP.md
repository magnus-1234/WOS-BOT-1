# Music System Setup Guide

The music functionality for the Whiteout Survival Bot requires a working **Lavalink** server. Lavalink is a standalone audio sending node based on Lavaplayer. 

If you are seeing the error *"The bot couldn't connect to the Lavalink server"*, it means the server configured in your `.env` file is offline or unreachable.

You have two options to fix this:

## Option 1: Use a Free Public Lavalink Server (Easiest)
There are community-maintained free Lavalink servers you can use. Note that they might occasionally go offline, requiring you to update your `.env` file again.

1. Open the `.env` file in the root directory.
2. Find the `Lavalink Configuration` section.
3. Replace the existing values with a working public node. Here are a few reliable lists to find one:
   - [DarrenOfficial's Lavalink List](https://github.com/DarrenOfficial/lavalink-list)
   - [AjieDev's Lavalink List](https://github.com/AjieDev/lavalink-list)

**Example Configuration for a public node:**
```env
LAVALINK_HOST=lavalink.oops.wtf
LAVALINK_PORT=2000
LAVALINK_PASSWORD=www.freelavalink.tube
LAVALINK_SECURE=false
```
*(Check the lists above for currently active nodes and their passwords)*

### Setting up Backup/Failover Nodes
You can configure up to 2 additional backup nodes. The bot will automatically attempt to connect to them, and if the primary node goes offline, playback can continue. 
Add the following to your `.env` file to set up secondary (`_2`) and tertiary (`_3`) fallback nodes:

```env
# Secondary Node (Backup 1)
LAVALINK_HOST_2=node2.example.com
LAVALINK_PORT_2=2333
LAVALINK_PASSWORD_2=youshallnotpass
LAVALINK_SECURE_2=false

# Tertiary Node (Backup 2)
LAVALINK_HOST_3=node3.example.com
LAVALINK_PORT_3=2333
LAVALINK_PASSWORD_3=youshallnotpass
LAVALINK_SECURE_3=false
```

4. Restart your bot (`run.bat` or `run.ps1`) for the changes to take effect.

---

## Option 2: Host Lavalink Locally (Most Reliable)
For a permanent and reliable setup, you can host Lavalink on the same machine as the bot.

### Prerequisites:
- **Java 17 or newer**: You must install Java on your machine. You can download it from [Adoptium (Temurin 17)](https://adoptium.net/).

### Setup Steps:
1. Download the latest `Lavalink.jar` from the [Lavalink GitHub Releases page](https://github.com/lavalink-devs/Lavalink/releases).
2. Place the `Lavalink.jar` file in your `Whiteout Survival Bot` folder (the same folder as `application.yml`).
3. You already have an `application.yml` file configured. Ensure it contains the following:
```yaml
server: 
  port: 2333
  address: 0.0.0.0
lavalink:
  server:
    password: "youshallnotpass"
```
4. Run Lavalink by opening a terminal in the folder and typing:
```cmd
java -jar Lavalink.jar
```
*(Alternatively, you can create a `run_lavalink.bat` file with that command)*
5. Update your `.env` file to connect to your local Lavalink:
```env
LAVALINK_HOST=127.0.0.1
LAVALINK_PORT=2333
LAVALINK_PASSWORD=youshallnotpass
LAVALINK_SECURE=false
```
6. Keep the Lavalink terminal window open, and run your Discord bot as usual.
