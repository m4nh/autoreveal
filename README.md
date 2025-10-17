# [AUTO]Reveal.js

<table>
<tr>
<td width="40%">
<img src="dist/images/autorevealjs.jpeg" alt="AutoReveal.js Logo" width="100%" />
</td>
<td width="60%">

This is a special python tool to easily create Reveal.js presentations using markdown files
organized in multiple folders. It automatically generates a single Reveal.js presentation merging
all chunks found and a lot of more amenities to make your life easier, to make your life better, 
to make you have a life.

</td>
</tr>
</table>

## Install & Run

Install python *requirements.txt* in your environment. Then run:

```bash
python autoreveal.py
```

## How to use

The server will generate a Presentation in `index.html` merging all the slides found in the `slides/` folder.
The syntax for each single slide is the standard [Reveal.js](https://revealjs.com/) one, with support for markdown slides, code, media, mermaid diagrams, etc.

Try to modify the slides in the `slides/` folder and re-run the server to see the changes reflected in the presentation!

## Auto Reload

You can enable auto-build and auto-reload of the presentation (on a custom port) on file changes by running:

```bash
python autoreveal.py --watch --live-reload --port 8085
```

## Customize Presentation

You can customize the presentation by modifying the `base.html` file, changing themes, adding custom CSS, scripts and more. To customize LOGO just place a `logo.png` file in the root folder.

## Export as PDF

Once you are satisfied with your presentation, and the server running with:

```bash
python autoreveal.py --port 8085
```

You can run [*decktape*](https://github.com/astefanutti/decktape) to export the presentation as PDF:

```bash
decktape http://localhost:8085 output.pdf
```