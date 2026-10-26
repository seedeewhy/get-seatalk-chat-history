# How it works — in plain English

*No jargon. This explains what the skill is doing under the hood so a non-engineer can trust it.*

## The one-sentence version

SeaTalk keeps a private, scrambled copy of all your chats in a hidden folder on your Mac; this skill unscrambles **your own** copy and reads it back to you with real names and emails attached.

## The slightly longer version

Think of SeaTalk's desktop app as having a **locked filing cabinet** on your computer. Every message you've ever sent or received is filed in there — but written in code so that if someone copied the files, they'd see gibberish.

Three things are needed to read it, and the skill has all three built in:

1. **The key to the cabinet.** SeaTalk locks the files with a password made of two parts: a fixed code hidden inside the SeaTalk app itself (the same for everyone who has that version of SeaTalk), plus *your* personal user number. The skill already knows the fixed code and reads your user number automatically. Because half the key is *your* user number and the files are readable only by *your* Mac account, this can never open someone else's chats — only yours.

2. **The right "unlock" method.** SeaTalk doesn't use the most common locking method — it uses a slightly different one. The first attempts to open it failed because we tried the wrong method. Once we matched SeaTalk's exact method, it opened cleanly. The skill uses the correct method every time.

3. **A phone book.** The chat files store people as numbers (e.g. "user 73711 said hi"), not names. SeaTalk keeps a separate address book — also on your Mac — listing everyone's name and Shopee email. The skill reads that address book and swaps the numbers for real names and emails, so you see "Amanda Thng said hi", not "user 73711 said hi". The same address book is how it figures out group names ("AI Champions!!") and who's in each group.

## What you get back

- **Whole conversations** — any group or any 1:1 chat, newest messages first, grouped by day.
- **Real names and Shopee emails** on every message and in group member lists.
- **Threads** — the little reply-chains under a message, reconstructed in order.
- **Reactions** (👍 🙏), **edits**, **quotes/replies**, and **link previews**.
- **Search** — find a keyword across everything, or inside one chat.
- **Images** — *best effort.* SeaTalk doesn't keep picture files on your Mac; it downloads them when you look at them and keeps them briefly. So the skill can recover pictures you've **looked at recently**, but not ones you never opened.

## What it deliberately does NOT do

- It **never changes** your SeaTalk — it makes a temporary copy of the files and reads that, so the app is untouched.
- It **can't read anyone else's** SeaTalk. It only works on the account logged into the Mac it runs on.
- It **doesn't send messages.** Reading and sending are two different problems (sending needs the live app or a bot). This skill only reads.

## Why it's safe to keep the "key" in the skill

The fixed code baked into the skill is not a personal secret — it's the same code sitting inside every copy of the SeaTalk app already. On its own it's useless: it only works when run *on your Mac*, *by your account*, against *your* files, which the operating system already protects. So baking it in just saves your assistant from re-discovering it every time — it doesn't expose anything that wasn't already on your machine.
